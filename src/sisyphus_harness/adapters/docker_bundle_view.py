from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from typing import Callable

from ..contracts.codec import loads_strict_json
from ..contracts.workspace import WorkspaceBundleRef
from .docker_runtime import DockerVerifierError


_BUNDLE_REFERENCE_LIMIT = 64 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
StableFileComparator = Callable[[os.stat_result, os.stat_result], bool]


def prepare_bundle_view(
    bundle_store: Path,
    reference: WorkspaceBundleRef,
    destination: Path,
    *,
    same_stable_file: StableFileComparator,
) -> None:
    try:
        directory_before = os.stat(bundle_store, follow_symlinks=False)
    except OSError as exc:
        raise DockerVerifierError(
            "workspace bundle store is not a regular directory"
        ) from exc
    if not stat.S_ISDIR(directory_before.st_mode):
        raise DockerVerifierError(
            "workspace bundle store is not a regular directory"
        )
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        directory_descriptor = os.open(bundle_store, flags)
    except OSError as exc:
        raise DockerVerifierError(
            "workspace bundle store is not a regular directory"
        ) from exc
    directory_opened = os.fstat(directory_descriptor)
    if not same_stable_file(directory_before, directory_opened):
        os.close(directory_descriptor)
        raise DockerVerifierError(
            "workspace bundle store changed while being opened"
        )

    digest = reference.archive_sha256.removeprefix("sha256:")
    archive_name = f"{digest}.tar"
    reference_name = f"{digest}.json"
    try:
        destination.mkdir(mode=0o700)
        _copy_stable_regular_file(
            directory_descriptor,
            archive_name,
            destination / archive_name,
            max_bytes=reference.size_bytes,
            same_stable_file=same_stable_file,
            expected_size=reference.size_bytes,
            expected_sha256=digest,
        )
        reference_bytes = _copy_stable_regular_file(
            directory_descriptor,
            reference_name,
            destination / reference_name,
            max_bytes=_BUNDLE_REFERENCE_LIMIT,
            same_stable_file=same_stable_file,
        )
        directory_after = os.stat(bundle_store, follow_symlinks=False)
        if not same_stable_file(directory_opened, directory_after):
            raise ValueError("workspace bundle store changed while being copied")
    except (OSError, ValueError) as exc:
        raise DockerVerifierError(
            "workspace bundle failed isolated-view validation"
        ) from exc
    finally:
        os.close(directory_descriptor)

    try:
        stored = WorkspaceBundleRef.from_dict(
            loads_strict_json(
                reference_bytes.decode("utf-8"),
                label="workspace bundle reference",
            )
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise DockerVerifierError(
            "workspace bundle reference failed host validation"
        ) from exc
    if stored != reference:
        raise DockerVerifierError(
            "workspace bundle reference does not match the request"
        )
    for path in destination.iterdir():
        path.chmod(0o444)
    destination.chmod(0o555)


def same_stable_file(before: os.stat_result, after: os.stat_result) -> bool:
    fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    return all(getattr(before, field) == getattr(after, field) for field in fields)


def _copy_stable_regular_file(
    directory_descriptor: int,
    name: str,
    destination: Path,
    *,
    max_bytes: int,
    same_stable_file: StableFileComparator,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> bytes:
    path_before = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    if not stat.S_ISREG(path_before.st_mode):
        raise ValueError("workspace bundle object is not a regular file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    captured = bytearray()
    digest = hashlib.sha256()
    copied = 0
    try:
        before = os.fstat(descriptor)
        if not same_stable_file(path_before, before):
            raise ValueError("workspace bundle object changed while being opened")
        if before.st_size > max_bytes:
            raise ValueError("workspace bundle object exceeds its declared limit")
        if expected_size is not None and before.st_size != expected_size:
            raise ValueError("workspace bundle archive size mismatch")
        with destination.open("xb") as output:
            while True:
                chunk = os.read(
                    descriptor,
                    min(_READ_CHUNK_BYTES, max_bytes - copied + 1),
                )
                if not chunk:
                    break
                copied += len(chunk)
                if copied > max_bytes:
                    raise ValueError(
                        "workspace bundle object exceeds its declared limit"
                    )
                digest.update(chunk)
                output.write(chunk)
                if expected_sha256 is None:
                    captured.extend(chunk)
            output.flush()
            os.fsync(output.fileno())
        after = os.fstat(descriptor)
        current = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if not same_stable_file(before, after) or not same_stable_file(
            before,
            current,
        ):
            raise ValueError("workspace bundle object changed while being copied")
        if expected_size is not None and copied != expected_size:
            raise ValueError("workspace bundle archive size mismatch")
        if expected_sha256 is not None and digest.hexdigest() != expected_sha256:
            raise ValueError("workspace bundle archive digest mismatch")
        return bytes(captured)
    finally:
        os.close(descriptor)
