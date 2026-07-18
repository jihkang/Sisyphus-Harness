from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile
from typing import BinaryIO

from ..contracts.workspace import WorkspaceBundleRef, WorkspaceSnapshot
from ..receipts import write_json_atomic
from ..workspace import snapshot_workspace


_BUNDLE_MANIFEST = ".sisyphus-workspace-bundle.json"
_MANIFEST_SCHEMA = "sisyphus_harness.workspace_bundle_manifest.v1"
_TREE_SCHEMA = "sisyphus_harness.workspace_tree.v1"


class WorkspaceBundleError(RuntimeError):
    pass


class FilesystemWorkspaceBundleStore:
    def __init__(
        self,
        root: Path,
        *,
        max_entries: int = 100_000,
        max_file_bytes: int = 256 * 1024 * 1024,
        max_bundle_bytes: int = 1024 * 1024 * 1024,
    ) -> None:
        if min(max_entries, max_file_bytes, max_bundle_bytes) <= 0:
            raise ValueError("workspace bundle limits must be positive")
        self.root = root
        self.max_entries = max_entries
        self.max_file_bytes = max_file_bytes
        self.max_bundle_bytes = max_bundle_bytes

    def create(self, workspace: Path) -> WorkspaceBundleRef:
        source_root = workspace.resolve()
        baseline = snapshot_workspace(source_root)
        paths = _git_workspace_paths(source_root)
        if len(paths) > self.max_entries:
            raise WorkspaceBundleError("workspace bundle exceeds entry limit")
        if _BUNDLE_MANIFEST in paths:
            raise WorkspaceBundleError("workspace contains reserved bundle manifest path")

        self.root.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.NamedTemporaryFile(
            prefix="workspace-",
            suffix=".tar.tmp",
            dir=self.root,
            delete=False,
        )
        temporary_path = Path(temporary.name)
        temporary.close()
        entries: list[dict[str, object]] = []
        total_bytes = 0
        try:
            with tarfile.open(
                temporary_path,
                mode="w",
                format=tarfile.GNU_FORMAT,
            ) as archive:
                for relative in paths:
                    path = source_root.joinpath(*PurePosixPath(relative).parts)
                    try:
                        metadata = path.lstat()
                    except FileNotFoundError:
                        continue
                    if stat.S_ISLNK(metadata.st_mode):
                        entry = _add_symlink(archive, source_root, path, relative)
                    elif stat.S_ISREG(metadata.st_mode):
                        entry = _add_file(
                            archive,
                            path,
                            relative,
                            metadata.st_mode,
                            max_file_bytes=self.max_file_bytes,
                        )
                        total_bytes += int(entry["size_bytes"])
                        if total_bytes > self.max_bundle_bytes:
                            raise WorkspaceBundleError(
                                "workspace bundle exceeds uncompressed size limit"
                            )
                    else:
                        raise WorkspaceBundleError(
                            f"workspace entry has unsupported type: {relative}"
                        )
                    entries.append(entry)

                final = snapshot_workspace(source_root)
                if final != baseline:
                    raise WorkspaceBundleError(
                        "workspace changed while its bundle was being created"
                    )
                entries.sort(key=lambda entry: str(entry["path"]))
                tree_hash = _tree_hash(entries)
                manifest = {
                    "schema_version": _MANIFEST_SCHEMA,
                    "source_snapshot": baseline.to_dict(),
                    "tree_hash": tree_hash,
                    "entries": entries,
                }
                _add_bytes(
                    archive,
                    _BUNDLE_MANIFEST,
                    _canonical_json(manifest) + b"\n",
                    mode=0o644,
                )

            archive_size = temporary_path.stat().st_size
            if archive_size > self.max_bundle_bytes:
                raise WorkspaceBundleError("workspace archive exceeds size limit")
            archive_digest = _sha256_file(temporary_path)
            ref = WorkspaceBundleRef(
                bundle_id=f"workspace:sha256:{archive_digest}",
                archive_sha256=f"sha256:{archive_digest}",
                size_bytes=archive_size,
                source_commit_sha=baseline.commit_sha,
                source_state_hash=baseline.state_hash,
                tree_hash=tree_hash,
                changed_paths=baseline.changed_paths,
                entry_count=len(entries),
            )
            archive_path = self._archive_path(ref)
            if archive_path.exists():
                if archive_path.is_symlink():
                    raise WorkspaceBundleError(
                        "workspace bundle archive cannot be a symlink"
                    )
                if (
                    archive_path.stat().st_size != archive_size
                    or _sha256_file(archive_path) != archive_digest
                ):
                    raise WorkspaceBundleError("workspace bundle digest collision")
                temporary_path.unlink()
            else:
                os.replace(temporary_path, archive_path)
            write_json_atomic(self._reference_path(ref), ref.to_dict())
            return ref
        finally:
            temporary_path.unlink(missing_ok=True)

    def load(self, bundle_id: str) -> WorkspaceBundleRef:
        digest = _digest_from_bundle_id(bundle_id)
        reference_path = self.root / f"{digest}.json"
        if reference_path.is_symlink():
            raise WorkspaceBundleError(
                f"workspace bundle reference cannot be a symlink: {bundle_id}"
            )
        try:
            raw = json.loads(reference_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise WorkspaceBundleError(
                f"workspace bundle reference not found: {bundle_id}"
            ) from exc
        try:
            ref = WorkspaceBundleRef.from_dict(raw)
        except ValueError as exc:
            raise WorkspaceBundleError(f"invalid workspace bundle reference: {exc}") from exc
        if ref.bundle_id != bundle_id:
            raise WorkspaceBundleError("workspace bundle reference ID mismatch")
        return ref

    def materialize(self, ref: WorkspaceBundleRef, destination: Path) -> WorkspaceSnapshot:
        if ref.size_bytes > self.max_bundle_bytes:
            raise WorkspaceBundleError("workspace bundle exceeds archive size limit")
        if ref.entry_count > self.max_entries:
            raise WorkspaceBundleError("workspace bundle exceeds entry limit")
        archive_path = self._archive_path(ref)
        if archive_path.is_symlink() or not archive_path.is_file():
            raise WorkspaceBundleError(f"workspace bundle archive not found: {ref.bundle_id}")
        if archive_path.stat().st_size != ref.size_bytes:
            raise WorkspaceBundleError("workspace bundle archive size mismatch")
        if f"sha256:{_sha256_file(archive_path)}" != ref.archive_sha256:
            raise WorkspaceBundleError("workspace bundle archive digest mismatch")

        if destination.is_symlink():
            raise WorkspaceBundleError(
                f"materialization destination is a symlink: {destination}"
            )
        target = destination.resolve(strict=False)
        if target.exists():
            raise WorkspaceBundleError(f"materialization destination exists: {destination}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent)
        )
        try:
            manifest, extracted_entries = self._extract(archive_path, temporary)
            _validate_manifest(manifest, ref, extracted_entries)
            snapshot = snapshot_materialized_workspace(
                temporary,
                source_commit_sha=ref.source_commit_sha,
                changed_paths=ref.changed_paths,
            )
            if snapshot.state_hash != ref.tree_hash:
                raise WorkspaceBundleError("materialized workspace tree hash mismatch")
            os.replace(temporary, target)
            return snapshot
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def _extract(
        self,
        archive_path: Path,
        destination: Path,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        manifest: dict[str, object] | None = None
        entries: list[dict[str, object]] = []
        seen: set[str] = set()
        total_bytes = 0
        with tarfile.open(archive_path, mode="r:*") as archive:
            for member in archive:
                relative = _validate_archive_path(member.name)
                if relative in seen:
                    raise WorkspaceBundleError(
                        f"workspace archive contains duplicate path: {relative}"
                    )
                seen.add(relative)
                if relative == _BUNDLE_MANIFEST:
                    if not member.isfile() or member.size > 1024 * 1024:
                        raise WorkspaceBundleError("workspace bundle manifest is invalid")
                    manifest_file = archive.extractfile(member)
                    if manifest_file is None:
                        raise WorkspaceBundleError("workspace bundle manifest is missing")
                    try:
                        parsed = json.loads(manifest_file.read().decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise WorkspaceBundleError(
                            "workspace bundle manifest is invalid JSON"
                        ) from exc
                    if not isinstance(parsed, dict):
                        raise WorkspaceBundleError(
                            "workspace bundle manifest must be an object"
                        )
                    manifest = parsed
                    continue
                if len(entries) >= self.max_entries:
                    raise WorkspaceBundleError("workspace archive exceeds entry limit")
                target = _safe_extraction_target(destination, relative)
                _ensure_safe_parent(destination, target.parent)
                if member.isfile():
                    if member.size < 0 or member.size > self.max_file_bytes:
                        raise WorkspaceBundleError(
                            f"workspace archive file exceeds limit: {relative}"
                        )
                    total_bytes += member.size
                    if total_bytes > self.max_bundle_bytes:
                        raise WorkspaceBundleError(
                            "workspace archive exceeds uncompressed size limit"
                        )
                    source = archive.extractfile(member)
                    if source is None:
                        raise WorkspaceBundleError(
                            f"workspace archive file is unreadable: {relative}"
                        )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = _copy_member(source, target, expected_size=member.size)
                    executable = bool(member.mode & 0o111)
                    target.chmod(0o755 if executable else 0o644)
                    entries.append(
                        {
                            "path": relative,
                            "kind": "file",
                            "size_bytes": member.size,
                            "sha256": f"sha256:{digest}",
                            "executable": executable,
                        }
                    )
                elif member.issym():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    link_target = _validate_symlink_target(
                        destination,
                        target,
                        member.linkname,
                    )
                    target.symlink_to(link_target)
                    entries.append(
                        {
                            "path": relative,
                            "kind": "symlink",
                            "target": link_target,
                        }
                    )
                else:
                    raise WorkspaceBundleError(
                        f"workspace archive entry type is forbidden: {relative}"
                    )
        if manifest is None:
            raise WorkspaceBundleError("workspace bundle manifest is missing")
        entries.sort(key=lambda entry: str(entry["path"]))
        return manifest, entries

    def _archive_path(self, ref: WorkspaceBundleRef) -> Path:
        return self.root / f"{_digest_from_bundle_id(ref.bundle_id)}.tar"

    def _reference_path(self, ref: WorkspaceBundleRef) -> Path:
        return self.root / f"{_digest_from_bundle_id(ref.bundle_id)}.json"


def snapshot_materialized_workspace(
    workspace: Path,
    *,
    source_commit_sha: str,
    changed_paths: tuple[str, ...] = (),
) -> WorkspaceSnapshot:
    root = workspace.resolve()
    if not root.is_dir():
        raise WorkspaceBundleError(f"materialized workspace does not exist: {workspace}")
    entries = _scan_tree(root)
    return WorkspaceSnapshot(
        commit_sha=source_commit_sha,
        state_hash=_tree_hash(entries),
        changed_paths=changed_paths,
    )


def _git_workspace_paths(workspace: Path) -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=workspace,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise WorkspaceBundleError(detail or "failed to enumerate workspace files")
    try:
        paths = tuple(
            item.decode("utf-8", errors="strict")
            for item in completed.stdout.split(b"\0")
            if item
        )
    except UnicodeDecodeError as exc:
        raise WorkspaceBundleError("workspace paths must be valid UTF-8") from exc
    normalized = tuple(sorted(_validate_archive_path(path) for path in paths))
    if len(set(normalized)) != len(normalized):
        raise WorkspaceBundleError("workspace contains duplicate normalized paths")
    return normalized


def _add_file(
    archive: tarfile.TarFile,
    path: Path,
    relative: str,
    mode: int,
    *,
    max_file_bytes: int,
) -> dict[str, object]:
    digest = hashlib.sha256()
    size = 0
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as content:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                size += len(chunk)
                if size > max_file_bytes:
                    raise WorkspaceBundleError(
                        f"workspace file exceeds bundle limit: {relative}"
                    )
                digest.update(chunk)
                content.write(chunk)
        content.seek(0)
        executable = bool(mode & 0o111)
        _add_stream(
            archive,
            relative,
            content,
            size=size,
            mode=0o755 if executable else 0o644,
        )
    return {
        "path": relative,
        "kind": "file",
        "size_bytes": size,
        "sha256": f"sha256:{digest.hexdigest()}",
        "executable": executable,
    }


def _add_symlink(
    archive: tarfile.TarFile,
    workspace: Path,
    path: Path,
    relative: str,
) -> dict[str, object]:
    target = os.readlink(path)
    _validate_symlink_target(workspace, path, target)
    info = _tar_info(relative, mode=0o777)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    info.size = 0
    archive.addfile(info)
    return {"path": relative, "kind": "symlink", "target": target}


def _add_bytes(
    archive: tarfile.TarFile,
    relative: str,
    content: bytes,
    *,
    mode: int,
) -> None:
    with tempfile.SpooledTemporaryFile() as stream:
        stream.write(content)
        stream.seek(0)
        _add_stream(archive, relative, stream, size=len(content), mode=mode)


def _add_stream(
    archive: tarfile.TarFile,
    relative: str,
    stream: BinaryIO,
    *,
    size: int,
    mode: int,
) -> None:
    info = _tar_info(relative, mode=mode)
    info.size = size
    archive.addfile(info, stream)


def _tar_info(relative: str, *, mode: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(relative)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


def _scan_tree(root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []

    def visit(directory: Path) -> None:
        with os.scandir(directory) as children:
            ordered = sorted(children, key=lambda child: child.name)
        for child in ordered:
            path = Path(child.path)
            relative = path.relative_to(root).as_posix()
            _validate_archive_path(relative)
            if child.is_symlink():
                target = os.readlink(path)
                _validate_symlink_target(root, path, target)
                entries.append(
                    {"path": relative, "kind": "symlink", "target": target}
                )
            elif child.is_file(follow_symlinks=False):
                metadata = path.stat(follow_symlinks=False)
                entries.append(
                    {
                        "path": relative,
                        "kind": "file",
                        "size_bytes": metadata.st_size,
                        "sha256": f"sha256:{_sha256_file(path)}",
                        "executable": bool(metadata.st_mode & 0o111),
                    }
                )
            elif child.is_dir(follow_symlinks=False):
                visit(path)
            else:
                raise WorkspaceBundleError(
                    f"materialized workspace contains unsupported entry: {relative}"
                )

    visit(root)
    entries.sort(key=lambda entry: str(entry["path"]))
    return entries


def _validate_manifest(
    manifest: dict[str, object],
    ref: WorkspaceBundleRef,
    entries: list[dict[str, object]],
) -> None:
    if set(manifest) != {"schema_version", "source_snapshot", "tree_hash", "entries"}:
        raise WorkspaceBundleError("workspace bundle manifest fields are invalid")
    if manifest["schema_version"] != _MANIFEST_SCHEMA:
        raise WorkspaceBundleError("unsupported workspace bundle manifest schema")
    source = manifest["source_snapshot"]
    expected_source = {
        "commit_sha": ref.source_commit_sha,
        "state_hash": ref.source_state_hash,
        "changed_paths": list(ref.changed_paths),
    }
    if source != expected_source:
        raise WorkspaceBundleError("workspace bundle source snapshot mismatch")
    if manifest["tree_hash"] != ref.tree_hash:
        raise WorkspaceBundleError("workspace bundle manifest tree hash mismatch")
    if manifest["entries"] != entries or len(entries) != ref.entry_count:
        raise WorkspaceBundleError("workspace bundle manifest entry mismatch")
    if _tree_hash(entries) != ref.tree_hash:
        raise WorkspaceBundleError("workspace bundle entry hash mismatch")


def _tree_hash(entries: list[dict[str, object]]) -> str:
    payload = {"schema_version": _TREE_SCHEMA, "entries": entries}
    return f"sha256:{hashlib.sha256(_canonical_json(payload)).hexdigest()}"


def _validate_archive_path(raw: str) -> str:
    candidate = PurePosixPath(raw)
    if (
        not raw
        or "\\" in raw
        or "\0" in raw
        or candidate.is_absolute()
        or candidate.as_posix() != raw
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise WorkspaceBundleError(f"workspace archive path is unsafe: {raw!r}")
    return raw


def _safe_extraction_target(root: Path, relative: str) -> Path:
    target = root.joinpath(*PurePosixPath(relative).parts)
    try:
        target.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise WorkspaceBundleError(
            f"workspace archive path escapes destination: {relative}"
        ) from exc
    return target


def _ensure_safe_parent(root: Path, parent: Path) -> None:
    current = parent
    while current != root:
        if current.is_symlink():
            raise WorkspaceBundleError("workspace archive path traverses a symlink")
        current = current.parent


def _validate_symlink_target(root: Path, link: Path, raw_target: str) -> str:
    if not raw_target or "\0" in raw_target or "\\" in raw_target:
        raise WorkspaceBundleError("workspace symlink target is unsafe")
    candidate = PurePosixPath(raw_target)
    if candidate.is_absolute():
        raise WorkspaceBundleError("workspace symlink target must be relative")
    target = link.parent.joinpath(*candidate.parts).resolve(strict=False)
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise WorkspaceBundleError("workspace symlink escapes bundle root") from exc
    return raw_target


def _copy_member(source: BinaryIO, target: Path, *, expected_size: int) -> str:
    digest = hashlib.sha256()
    written = 0
    with target.open("xb") as destination:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            written += len(chunk)
            if written > expected_size:
                raise WorkspaceBundleError("workspace archive member exceeds declared size")
            digest.update(chunk)
            destination.write(chunk)
        destination.flush()
        os.fsync(destination.fileno())
    if written != expected_size:
        raise WorkspaceBundleError("workspace archive member size mismatch")
    return digest.hexdigest()


def _digest_from_bundle_id(bundle_id: str) -> str:
    prefix = "workspace:sha256:"
    digest = bundle_id.removeprefix(prefix)
    if not bundle_id.startswith(prefix) or len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise WorkspaceBundleError(f"invalid workspace bundle ID: {bundle_id}")
    return digest


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
