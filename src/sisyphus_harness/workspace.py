from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess

from .contracts.workspace import WorkspaceSnapshot


class PathBoundaryError(ValueError):
    pass


class WorkspaceStateError(RuntimeError):
    pass


def contained_path(root: Path, value: str | Path, *, require_relative: bool = False) -> Path:
    root_path = root.resolve()
    requested = Path(value)
    if require_relative and requested.is_absolute():
        raise PathBoundaryError(f"path must be relative to workspace: {value}")
    candidate = requested if requested.is_absolute() else root_path / requested
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise PathBoundaryError(f"path escapes workspace: {value}") from exc
    if require_relative and resolved == root_path:
        raise PathBoundaryError(f"path must identify a workspace child: {value}")
    return resolved


def snapshot_workspace(workspace: Path) -> WorkspaceSnapshot:
    root = workspace.resolve()
    if not root.is_dir():
        raise WorkspaceStateError(f"workspace does not exist: {workspace}")
    commit = _git(root, ["rev-parse", "HEAD"]).decode("utf-8").strip()
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) is None:
        raise WorkspaceStateError("workspace HEAD is not a full commit SHA")

    unstaged = _git(root, ["diff", "--binary", "--"])
    staged = _git(root, ["diff", "--cached", "--binary", "HEAD", "--"])
    tracked_paths = _nul_paths(
        _git(root, ["diff", "--name-only", "-z", "--"])
        + _git(root, ["diff", "--cached", "--name-only", "-z", "HEAD", "--"])
    )
    untracked_paths = _nul_paths(
        _git(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    )
    changed_paths = tuple(sorted(set(tracked_paths).union(untracked_paths)))

    digest = hashlib.sha256()
    digest.update(b"commit\0")
    digest.update(commit.encode("ascii"))
    digest.update(b"\0unstaged\0")
    digest.update(unstaged)
    digest.update(b"\0staged\0")
    digest.update(staged)
    for relative in sorted(untracked_paths):
        contained_path(root, relative, require_relative=True)
        path = root / relative
        digest.update(b"\0untracked\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(path).encode("utf-8"))
        elif path.is_file():
            digest.update(_sha256_file(path).encode("ascii"))
        else:
            digest.update(b"non-file")
    return WorkspaceSnapshot(
        commit_sha=commit,
        state_hash=f"sha256:{digest.hexdigest()}",
        changed_paths=changed_paths,
    )


def _git(workspace: Path, args: list[str]) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceStateError(f"git command timed out: {' '.join(args)}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise WorkspaceStateError(detail or f"git command failed: {' '.join(args)}")
    return completed.stdout


def _nul_paths(raw: bytes) -> tuple[str, ...]:
    paths: list[str] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        paths.append(item.decode("utf-8", errors="strict"))
    return tuple(paths)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
