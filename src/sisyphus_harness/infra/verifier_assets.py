from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile

from ..contracts.codec import canonical_json_bytes, loads_strict_json, sha256_digest
from ..contracts.verifier_assets import VerifierAssetBundleRef
from ..receipts import write_json_atomic


_MANIFEST_NAME = "manifest.json"
_FILES_DIRECTORY = "files"
_MANIFEST_SCHEMA = "sisyphus_harness.verifier_asset_manifest.v1"
_TREE_SCHEMA = "sisyphus_harness.verifier_asset_tree.v1"
_READ_CHUNK_BYTES = 1024 * 1024


class VerifierAssetError(RuntimeError):
    pass


class FilesystemVerifierAssetBundleStore:
    """Content-addressed storage for verifier-owned regular files.

    Asset bundles deliberately reject symlinks. Verifier checks and fixtures do
    not need link semantics, and admitting them would expand the mount boundary.
    """

    def __init__(
        self,
        root: Path,
        *,
        max_entries: int = 10_000,
        max_file_bytes: int = 64 * 1024 * 1024,
        max_bundle_bytes: int = 256 * 1024 * 1024,
        max_manifest_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if min(
            max_entries,
            max_file_bytes,
            max_bundle_bytes,
            max_manifest_bytes,
        ) <= 0:
            raise ValueError("verifier asset limits must be positive")
        self.root = root
        self.max_entries = max_entries
        self.max_file_bytes = max_file_bytes
        self.max_bundle_bytes = max_bundle_bytes
        self.max_manifest_bytes = max_manifest_bytes

    def create(self, source: Path) -> VerifierAssetBundleRef:
        source_metadata = _lstat_directory(source, "verifier asset source")
        self.root.mkdir(parents=True, exist_ok=True)
        _lstat_directory(self.root, "verifier asset store")
        temporary = Path(
            tempfile.mkdtemp(prefix=".verifier-assets-", dir=self.root)
        )
        files_root = temporary / _FILES_DIRECTORY
        files_root.mkdir(mode=0o700)
        source_descriptor = _open_directory(source)
        try:
            if not _same_stable_file(source_metadata, os.fstat(source_descriptor)):
                raise VerifierAssetError(
                    "verifier asset source changed while being opened"
                )
            entries: list[dict[str, object]] = []
            total_size = self._copy_source_directory(
                source_descriptor,
                files_root,
                prefix=(),
                entries=entries,
                total_size=0,
            )
            if not entries:
                raise VerifierAssetError(
                    "verifier asset source must contain at least one file"
                )
            if not _same_stable_file(
                source_metadata,
                os.stat(source, follow_symlinks=False),
            ):
                raise VerifierAssetError(
                    "verifier asset source changed while being copied"
                )
            entries.sort(key=lambda item: str(item["path"]))
            tree_hash = sha256_digest(
                {"schema_version": _TREE_SCHEMA, "entries": entries}
            )
            if verifier_asset_tree_hash(source) != tree_hash:
                raise VerifierAssetError(
                    "verifier asset source changed while being copied"
                )
            manifest = {
                "schema_version": _MANIFEST_SCHEMA,
                "tree_hash": tree_hash,
                "total_size_bytes": total_size,
                "entries": entries,
            }
            manifest_bytes = canonical_json_bytes(manifest) + b"\n"
            if len(manifest_bytes) > self.max_manifest_bytes:
                raise VerifierAssetError(
                    "verifier asset manifest exceeds size limit"
                )
            manifest_digest = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
            reference = VerifierAssetBundleRef(
                bundle_id=f"verifier-assets:{manifest_digest}",
                manifest_sha256=manifest_digest,
                tree_hash=tree_hash,
                total_size_bytes=total_size,
                entry_count=len(entries),
            )
            _write_new_file(temporary / _MANIFEST_NAME, manifest_bytes, mode=0o444)
            _fsync_tree(temporary)
            destination = self._bundle_path(reference)
            if destination.exists() or destination.is_symlink():
                reference_path = self._reference_path(reference)
                if reference_path.exists() or reference_path.is_symlink():
                    existing = self.load(reference.bundle_id)
                    if existing != reference:
                        raise VerifierAssetError(
                            "verifier asset bundle digest collision"
                        )
                else:
                    self._validate_published_bundle(reference)
                shutil.rmtree(temporary)
            else:
                os.replace(temporary, destination)
                _fsync_directory(self.root)
            write_json_atomic(self._reference_path(reference), reference.to_dict())
            return reference
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        finally:
            os.close(source_descriptor)

    def load(self, bundle_id: str) -> VerifierAssetBundleRef:
        digest = _digest_from_bundle_id(bundle_id)
        reference_path = self.root / f"{digest}.json"
        try:
            content = _read_stable_regular_file(
                reference_path,
                max_bytes=self.max_manifest_bytes,
            )
            reference = VerifierAssetBundleRef.from_dict(
                loads_strict_json(content, label="verifier asset reference")
            )
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise VerifierAssetError(
                f"verifier asset bundle reference is invalid: {bundle_id}"
            ) from exc
        if reference.bundle_id != bundle_id:
            raise VerifierAssetError("verifier asset bundle reference ID mismatch")
        return reference

    def materialize(
        self,
        reference: VerifierAssetBundleRef,
        destination: Path,
    ) -> str:
        if type(reference) is not VerifierAssetBundleRef:
            raise TypeError(
                "verifier asset materialization requires an exact bundle reference"
            )
        if reference.entry_count > self.max_entries:
            raise VerifierAssetError("verifier asset bundle exceeds entry limit")
        if reference.total_size_bytes > self.max_bundle_bytes:
            raise VerifierAssetError("verifier asset bundle exceeds size limit")
        stored = self.load(reference.bundle_id)
        if stored != reference:
            raise VerifierAssetError(
                "verifier asset reference does not match stored authority"
            )
        bundle_path = self._bundle_path(reference)
        bundle_metadata = _lstat_directory(bundle_path, "verifier asset bundle")
        bundle_descriptor = _open_directory(bundle_path)
        target = destination.resolve(strict=False)
        if destination.is_symlink() or target.exists():
            os.close(bundle_descriptor)
            raise VerifierAssetError(
                "verifier asset materialization destination already exists"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent)
        )
        try:
            if not _same_stable_file(bundle_metadata, os.fstat(bundle_descriptor)):
                raise VerifierAssetError(
                    "verifier asset bundle changed while being opened"
                )
            manifest = self._read_manifest(bundle_descriptor, reference)
            entries = _manifest_entries(manifest, reference)
            files_descriptor = _open_directory_at(bundle_descriptor, _FILES_DIRECTORY)
            try:
                total_size = 0
                for entry in entries:
                    relative = _manifest_path(entry)
                    size = _manifest_integer(entry, "size_bytes")
                    if size > self.max_file_bytes:
                        raise VerifierAssetError(
                            f"verifier asset file exceeds limit: {relative}"
                        )
                    total_size += size
                    if total_size > self.max_bundle_bytes:
                        raise VerifierAssetError(
                            "verifier asset bundle exceeds size limit"
                        )
                    destination_file = temporary.joinpath(
                        *PurePosixPath(relative).parts
                    )
                    destination_file.parent.mkdir(parents=True, exist_ok=True)
                    digest = _copy_relative_regular_file(
                        files_descriptor,
                        relative,
                        destination_file,
                        expected_size=size,
                        max_bytes=self.max_file_bytes,
                    )
                    if f"sha256:{digest}" != entry.get("sha256"):
                        raise VerifierAssetError(
                            f"verifier asset file digest mismatch: {relative}"
                        )
                    executable = entry.get("executable")
                    if type(executable) is not bool:
                        raise VerifierAssetError(
                            f"verifier asset executable flag is invalid: {relative}"
                        )
                    destination_file.chmod(0o555 if executable else 0o444)
            finally:
                os.close(files_descriptor)
            if total_size != reference.total_size_bytes:
                raise VerifierAssetError("verifier asset total size mismatch")
            if not _same_stable_file(
                bundle_metadata,
                os.stat(bundle_path, follow_symlinks=False),
            ):
                raise VerifierAssetError(
                    "verifier asset bundle changed while being copied"
                )
            tree_hash = verifier_asset_tree_hash(temporary)
            if tree_hash != reference.tree_hash:
                raise VerifierAssetError("verifier asset materialized tree mismatch")
            _make_directories_read_only(temporary)
            os.replace(temporary, target)
            _fsync_directory(target.parent)
            return tree_hash
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        finally:
            os.close(bundle_descriptor)

    def _copy_source_directory(
        self,
        directory_descriptor: int,
        destination: Path,
        *,
        prefix: tuple[str, ...],
        entries: list[dict[str, object]],
        total_size: int,
    ) -> int:
        before = os.fstat(directory_descriptor)
        try:
            names = sorted(os.listdir(directory_descriptor))
        except OSError as exc:
            raise VerifierAssetError("verifier asset directory is unreadable") from exc
        for name in names:
            _validate_name(name)
            relative_parts = (*prefix, name)
            relative = PurePosixPath(*relative_parts).as_posix()
            try:
                metadata = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise VerifierAssetError(
                    f"verifier asset entry is unreadable: {relative}"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise VerifierAssetError(
                    f"verifier asset symlinks are forbidden: {relative}"
                )
            if stat.S_ISDIR(metadata.st_mode):
                child = _open_directory_at(directory_descriptor, name)
                try:
                    if not _same_stable_file(metadata, os.fstat(child)):
                        raise VerifierAssetError(
                            f"verifier asset directory changed while opening: {relative}"
                        )
                    child_destination = destination / name
                    child_destination.mkdir(mode=0o700)
                    total_size = self._copy_source_directory(
                        child,
                        child_destination,
                        prefix=relative_parts,
                        entries=entries,
                        total_size=total_size,
                    )
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise VerifierAssetError(
                    f"verifier asset entry type is forbidden: {relative}"
                )
            if len(entries) >= self.max_entries:
                raise VerifierAssetError("verifier asset bundle exceeds entry limit")
            destination_file = destination / name
            digest, size = _copy_open_regular_file(
                directory_descriptor,
                name,
                metadata,
                destination_file,
                max_bytes=self.max_file_bytes,
            )
            total_size += size
            if total_size > self.max_bundle_bytes:
                raise VerifierAssetError("verifier asset bundle exceeds size limit")
            executable = bool(metadata.st_mode & 0o111)
            destination_file.chmod(0o755 if executable else 0o644)
            entries.append(
                {
                    "path": relative,
                    "size_bytes": size,
                    "sha256": f"sha256:{digest}",
                    "executable": executable,
                }
            )
        after = os.fstat(directory_descriptor)
        if not _same_stable_file(before, after):
            raise VerifierAssetError("verifier asset directory changed while scanning")
        return total_size

    def _read_manifest(
        self,
        bundle_descriptor: int,
        reference: VerifierAssetBundleRef,
    ) -> dict[str, object]:
        content = _read_regular_at(
            bundle_descriptor,
            _MANIFEST_NAME,
            max_bytes=self.max_manifest_bytes,
        )
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if digest != reference.manifest_sha256:
            raise VerifierAssetError("verifier asset manifest digest mismatch")
        try:
            manifest = loads_strict_json(content, label="verifier asset manifest")
        except ValueError as exc:
            raise VerifierAssetError("verifier asset manifest is invalid") from exc
        if not isinstance(manifest, dict):
            raise VerifierAssetError("verifier asset manifest must be an object")
        return manifest

    def _validate_published_bundle(
        self,
        reference: VerifierAssetBundleRef,
    ) -> None:
        bundle_path = self._bundle_path(reference)
        metadata = _lstat_directory(bundle_path, "verifier asset bundle")
        descriptor = _open_directory(bundle_path)
        try:
            if not _same_stable_file(metadata, os.fstat(descriptor)):
                raise VerifierAssetError(
                    "verifier asset bundle changed while being opened"
                )
            if set(os.listdir(descriptor)) != {_MANIFEST_NAME, _FILES_DIRECTORY}:
                raise VerifierAssetError("verifier asset bundle fields are invalid")
            manifest = self._read_manifest(descriptor, reference)
            _manifest_entries(manifest, reference)
            if verifier_asset_tree_hash(bundle_path / _FILES_DIRECTORY) != reference.tree_hash:
                raise VerifierAssetError("verifier asset stored tree mismatch")
            if not _same_stable_file(
                metadata,
                os.stat(bundle_path, follow_symlinks=False),
            ):
                raise VerifierAssetError(
                    "verifier asset bundle changed while being validated"
                )
        finally:
            os.close(descriptor)

    def _bundle_path(self, reference: VerifierAssetBundleRef) -> Path:
        return self.root / _digest_from_bundle_id(reference.bundle_id)

    def _reference_path(self, reference: VerifierAssetBundleRef) -> Path:
        return self.root / f"{_digest_from_bundle_id(reference.bundle_id)}.json"


def verifier_asset_tree_hash(root: Path) -> str:
    metadata = _lstat_directory(root, "verifier asset tree")
    descriptor = _open_directory(root)
    entries: list[dict[str, object]] = []
    try:
        if not _same_stable_file(metadata, os.fstat(descriptor)):
            raise VerifierAssetError("verifier asset tree changed while opening")
        _scan_tree(descriptor, prefix=(), entries=entries)
        if not _same_stable_file(
            metadata,
            os.stat(root, follow_symlinks=False),
        ):
            raise VerifierAssetError("verifier asset tree changed while scanning")
    finally:
        os.close(descriptor)
    entries.sort(key=lambda item: str(item["path"]))
    return sha256_digest({"schema_version": _TREE_SCHEMA, "entries": entries})


def _scan_tree(
    directory_descriptor: int,
    *,
    prefix: tuple[str, ...],
    entries: list[dict[str, object]],
) -> None:
    before = os.fstat(directory_descriptor)
    for name in sorted(os.listdir(directory_descriptor)):
        _validate_name(name)
        relative_parts = (*prefix, name)
        relative = PurePosixPath(*relative_parts).as_posix()
        metadata = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if stat.S_ISLNK(metadata.st_mode):
            raise VerifierAssetError(
                f"verifier asset tree contains a symlink: {relative}"
            )
        if stat.S_ISDIR(metadata.st_mode):
            child = _open_directory_at(directory_descriptor, name)
            try:
                if not _same_stable_file(metadata, os.fstat(child)):
                    raise VerifierAssetError(
                        f"verifier asset directory changed while opening: {relative}"
                    )
                _scan_tree(child, prefix=relative_parts, entries=entries)
            finally:
                os.close(child)
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise VerifierAssetError(
                f"verifier asset tree contains a forbidden entry: {relative}"
            )
        content = _read_regular_at(
            directory_descriptor,
            name,
            max_bytes=max(metadata.st_size, 1),
            expected=metadata,
        )
        entries.append(
            {
                "path": relative,
                "size_bytes": len(content),
                "sha256": f"sha256:{hashlib.sha256(content).hexdigest()}",
                "executable": bool(metadata.st_mode & 0o111),
            }
        )
    if not _same_stable_file(before, os.fstat(directory_descriptor)):
        raise VerifierAssetError("verifier asset directory changed while scanning")


def _manifest_entries(
    manifest: dict[str, object],
    reference: VerifierAssetBundleRef,
) -> list[dict[str, object]]:
    if set(manifest) != {
        "schema_version",
        "tree_hash",
        "total_size_bytes",
        "entries",
    }:
        raise VerifierAssetError("verifier asset manifest fields are invalid")
    if manifest["schema_version"] != _MANIFEST_SCHEMA:
        raise VerifierAssetError("unsupported verifier asset manifest schema")
    if manifest["tree_hash"] != reference.tree_hash:
        raise VerifierAssetError("verifier asset manifest tree hash mismatch")
    if manifest["total_size_bytes"] != reference.total_size_bytes:
        raise VerifierAssetError("verifier asset manifest size mismatch")
    entries = manifest["entries"]
    if not isinstance(entries, list) or len(entries) != reference.entry_count:
        raise VerifierAssetError("verifier asset manifest entry count mismatch")
    normalized: list[dict[str, object]] = []
    previous = ""
    for raw in entries:
        if not isinstance(raw, dict) or set(raw) != {
            "path",
            "size_bytes",
            "sha256",
            "executable",
        }:
            raise VerifierAssetError("verifier asset manifest entry is invalid")
        relative = _manifest_path(raw)
        if relative <= previous:
            raise VerifierAssetError(
                "verifier asset manifest paths must be unique and sorted"
            )
        previous = relative
        digest = raw["sha256"]
        if (
            not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or len(digest) != 71
            or any(character not in "0123456789abcdef" for character in digest[7:])
        ):
            raise VerifierAssetError("verifier asset file digest is invalid")
        _manifest_integer(raw, "size_bytes")
        if type(raw["executable"]) is not bool:
            raise VerifierAssetError("verifier asset executable flag is invalid")
        normalized.append(raw)
    tree_hash = sha256_digest(
        {"schema_version": _TREE_SCHEMA, "entries": normalized}
    )
    if tree_hash != reference.tree_hash:
        raise VerifierAssetError("verifier asset manifest entry hash mismatch")
    return normalized


def _manifest_path(entry: dict[str, object]) -> str:
    raw = entry.get("path")
    if not isinstance(raw, str):
        raise VerifierAssetError("verifier asset manifest path is invalid")
    candidate = PurePosixPath(raw)
    if (
        not raw
        or "\\" in raw
        or "\0" in raw
        or candidate.is_absolute()
        or candidate.as_posix() != raw
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise VerifierAssetError("verifier asset manifest path is unsafe")
    return raw


def _manifest_integer(entry: dict[str, object], field: str) -> int:
    raw = entry.get(field)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise VerifierAssetError(f"verifier asset manifest {field} is invalid")
    return raw


def _copy_open_regular_file(
    directory_descriptor: int,
    name: str,
    metadata: os.stat_result,
    destination: Path,
    *,
    max_bytes: int,
) -> tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
        opened = os.fstat(descriptor)
        if not _same_stable_file(metadata, opened) or not stat.S_ISREG(opened.st_mode):
            raise VerifierAssetError("verifier asset file changed while opening")
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            descriptor = -1
            with destination.open("xb") as target:
                for chunk in iter(lambda: source.read(_READ_CHUNK_BYTES), b""):
                    size += len(chunk)
                    if size > max_bytes:
                        raise VerifierAssetError("verifier asset file exceeds size limit")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if not _same_stable_file(opened, os.fstat(source.fileno())):
                raise VerifierAssetError("verifier asset file changed while reading")
        return digest.hexdigest(), size
    except OSError as exc:
        raise VerifierAssetError("verifier asset file could not be copied safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _copy_relative_regular_file(
    root_descriptor: int,
    relative: str,
    destination: Path,
    *,
    expected_size: int,
    max_bytes: int,
) -> str:
    parts = PurePosixPath(relative).parts
    descriptors: list[int] = []
    current = root_descriptor
    try:
        for part in parts[:-1]:
            child = _open_directory_at(current, part)
            descriptors.append(child)
            current = child
        metadata = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
        digest, size = _copy_open_regular_file(
            current,
            parts[-1],
            metadata,
            destination,
            max_bytes=max_bytes,
        )
        if size != expected_size:
            raise VerifierAssetError(
                f"verifier asset file size mismatch: {relative}"
            )
        return digest
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _read_stable_regular_file(path: Path, *, max_bytes: int) -> bytes:
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError("path is not a regular file")
    parent_descriptor = _open_directory(path.parent)
    try:
        return _read_regular_at(
            parent_descriptor,
            path.name,
            max_bytes=max_bytes,
            expected=metadata,
        )
    finally:
        os.close(parent_descriptor)


def _read_regular_at(
    directory_descriptor: int,
    name: str,
    *,
    max_bytes: int,
    expected: os.stat_result | None = None,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("path is not a regular file")
        if expected is not None and not _same_stable_file(expected, opened):
            raise OSError("file changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise OSError("file exceeds size limit")
        if not _same_stable_file(opened, os.fstat(descriptor)):
            raise OSError("file changed while reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_new_file(path: Path, content: bytes, *, mode: int) -> None:
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(mode)


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise VerifierAssetError("verifier asset directory could not be opened safely") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise VerifierAssetError("verifier asset path is not a directory")
    return descriptor


def _open_directory_at(parent_descriptor: int, name: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise VerifierAssetError("verifier asset directory traversal is unsafe") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise VerifierAssetError("verifier asset path is not a directory")
    return descriptor


def _lstat_directory(path: Path, label: str) -> os.stat_result:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise VerifierAssetError(f"{label} is not a directory") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise VerifierAssetError(f"{label} is not a regular directory")
    return metadata


def _validate_name(name: str) -> None:
    try:
        name.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise VerifierAssetError("verifier asset names must be valid UTF-8") from exc
    if not name or name in {".", ".."} or "/" in name or "\0" in name:
        raise VerifierAssetError("verifier asset name is unsafe")


def _digest_from_bundle_id(bundle_id: str) -> str:
    prefix = "verifier-assets:sha256:"
    digest = bundle_id.removeprefix(prefix)
    if not bundle_id.startswith(prefix) or len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise VerifierAssetError(f"invalid verifier asset bundle ID: {bundle_id}")
    return digest


def _same_stable_file(left: os.stat_result, right: os.stat_result) -> bool:
    fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    return all(getattr(left, field) == getattr(right, field) for field in fields)


def _make_directories_read_only(root: Path) -> None:
    directories = [root]
    directories.extend(path for path in root.rglob("*") if path.is_dir())
    for directory in reversed(directories):
        directory.chmod(0o555)


def _fsync_tree(root: Path) -> None:
    directories = [path for path in root.rglob("*") if path.is_dir()]
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _fsync_directory(directory)
    _fsync_directory(root)


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "FilesystemVerifierAssetBundleStore",
    "VerifierAssetError",
    "verifier_asset_tree_hash",
]
