from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import tempfile

from .workspace_tool_contracts import ToolError


class BoundedWorkspaceIO:
    def __init__(self, workspace: Path, *, max_file_bytes: int) -> None:
        self.workspace = workspace
        self.max_file_bytes = max_file_bytes

    def read_text(self, path: Path) -> str:
        with path.open("rb") as handle:
            raw = handle.read(self.max_file_bytes + 1)
        if len(raw) > self.max_file_bytes:
            raise ToolError(
                f"file exceeds {self.max_file_bytes} byte limit: "
                f"{path.relative_to(self.workspace)}"
            )
        if b"\0" in raw:
            raise ToolError(f"binary files are not supported: {path.name}")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"file is not UTF-8: {path.name}") from exc

    def check_content_size(self, content: str) -> None:
        if "\0" in content:
            raise ToolError("binary content is not supported")
        size = len(content.encode("utf-8"))
        if size > self.max_file_bytes:
            raise ToolError(f"content exceeds {self.max_file_bytes} byte limit")

    def write_text_atomic(self, path: Path, content: str) -> None:
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                delete=False,
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.chmod(temporary, mode)
            os.replace(temporary, path)
            fsync_directory(path.parent)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def delete_file(self, path: Path) -> None:
        path.unlink()
        fsync_directory(path.parent)


def require_expected_hash(content: str, expected: str | None) -> None:
    actual = sha256_bytes(content.encode("utf-8"))
    if expected is None:
        raise ToolError("existing files require expected_sha256")
    if expected != actual:
        raise ToolError(f"stale file hash: expected {expected}, current {actual}")


def sha256_bytes(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def truncate_text(content: str, limit: int) -> tuple[str, bool]:
    if len(content) <= limit:
        return content, False
    return content[:limit], True


def truncate_lines(lines: list[str], limit: int) -> tuple[list[str], bool]:
    result: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > limit:
            return result, True
        result.append(line)
        size += len(line) + 1
    return result, False


def fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
