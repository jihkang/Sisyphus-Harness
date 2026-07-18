from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from typing import Any

from .workspace import PathBoundaryError, contained_path


class ToolError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    tool: str
    output: dict[str, object]
    mutated: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "tool": self.tool,
            "output": self.output,
            "mutated": self.mutated,
        }


class WorkspaceTools:
    def __init__(
        self,
        workspace: Path,
        *,
        max_file_bytes: int,
        max_output_chars: int,
        protected_write_paths: tuple[Path, ...] = (),
    ) -> None:
        self.workspace = workspace.resolve()
        self.max_file_bytes = max_file_bytes
        self.max_output_chars = max_output_chars
        try:
            self.protected_write_paths = tuple(
                contained_path(self.workspace, path)
                for path in protected_write_paths
            )
        except PathBoundaryError as exc:
            raise ToolError(f"protected write path is outside workspace: {exc}") from exc

    def execute(self, tool: str, arguments: dict[str, Any]) -> ToolOutcome:
        handlers = {
            "list_files": self._list_files,
            "read_file": self._read_file,
            "search_text": self._search_text,
            "write_file": self._write_file,
            "replace_text": self._replace_text,
            "delete_file": self._delete_file,
        }
        handler = handlers.get(tool)
        if handler is None:
            raise ToolError(f"unsupported tool: {tool}")
        return handler(arguments)

    def _list_files(self, arguments: dict[str, Any]) -> ToolOutcome:
        _reject_unknown(arguments, {"prefix"})
        prefix = arguments.get("prefix", "")
        if not isinstance(prefix, str):
            raise ToolError("list_files.prefix must be a string")
        normalized_prefix = _normalize_scope_path(prefix)
        if normalized_prefix:
            self._read_path(normalized_prefix, require_file=False)
        completed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=self.workspace,
            capture_output=True,
            timeout=15,
            check=False,
        )
        if completed.returncode != 0:
            raise ToolError(
                completed.stderr.decode("utf-8", errors="replace").strip()
                or "git ls-files failed"
            )
        files = sorted(
            item.decode("utf-8")
            for item in completed.stdout.split(b"\0")
            if item
        )
        if normalized_prefix:
            files = [
                path
                for path in files
                if path == normalized_prefix or path.startswith(f"{normalized_prefix}/")
            ]
        rendered, truncated = _truncate_lines(files, self.max_output_chars)
        return ToolOutcome(
            tool="list_files",
            output={
                "files": rendered,
                "total_count": len(files),
                "truncated": truncated,
            },
            mutated=False,
        )

    def _read_file(self, arguments: dict[str, Any]) -> ToolOutcome:
        _reject_unknown(arguments, {"path", "start_line", "end_line"})
        relative = _required_string(arguments, "path")
        path = self._read_path(relative)
        start_line = _optional_positive_int(
            arguments.get("start_line"),
            "start_line",
            1,
        )
        end_line = _optional_positive_int(
            arguments.get("end_line"),
            "end_line",
            None,
        )
        assert start_line is not None
        if end_line is not None and end_line < start_line:
            raise ToolError("read_file.end_line must be greater than or equal to start_line")
        content = self._read_text(path)
        lines = content.splitlines(keepends=True)
        selected = "".join(lines[start_line - 1 : end_line])
        selected, truncated = _truncate_text(selected, self.max_output_chars)
        return ToolOutcome(
            tool="read_file",
            output={
                "path": relative,
                "sha256": _sha256_bytes(content.encode("utf-8")),
                "start_line": start_line,
                "end_line": min(end_line or len(lines), len(lines)),
                "total_lines": len(lines),
                "content": selected,
                "truncated": truncated,
            },
            mutated=False,
        )

    def _search_text(self, arguments: dict[str, Any]) -> ToolOutcome:
        _reject_unknown(arguments, {"query", "path", "max_results"})
        query = _required_string(arguments, "query")
        if len(query) > 512:
            raise ToolError("search_text.query must be at most 512 characters")
        raw_path = arguments.get("path", "")
        if not isinstance(raw_path, str):
            raise ToolError("search_text.path must be a string")
        prefix = _normalize_scope_path(raw_path)
        max_results = _optional_positive_int(
            arguments.get("max_results"),
            "max_results",
            50,
        )
        max_results = min(max_results, 200)
        files = self._tracked_and_untracked_files()
        if prefix:
            self._read_path(prefix, require_file=False)
            files = [
                path
                for path in files
                if path == prefix or path.startswith(f"{prefix}/")
            ]
        matches: list[dict[str, object]] = []
        skipped: list[str] = []
        rendered_size = 0
        for relative in files:
            path = self._read_path(relative)
            try:
                content = self._read_text(path)
            except ToolError:
                skipped.append(relative)
                continue
            for line_number, line in enumerate(content.splitlines(), start=1):
                if query in line:
                    match = {
                        "path": relative,
                        "line": line_number,
                        "text": line[:1000],
                    }
                    match_size = len(str(match))
                    if (
                        len(matches) >= max_results
                        or rendered_size + match_size > self.max_output_chars
                    ):
                        return ToolOutcome(
                            tool="search_text",
                            output={
                                "matches": matches,
                                "query_mode": "literal",
                                "truncated": True,
                                "skipped_files": skipped[:20],
                            },
                            mutated=False,
                        )
                    matches.append(match)
                    rendered_size += match_size
        output: dict[str, object] = {
            "matches": matches,
            "query_mode": "literal",
            "truncated": False,
            "skipped_files": skipped[:20],
        }
        if not matches:
            output["hint"] = (
                "Search uses an exact literal substring. Use an unescaped literal, "
                "or inspect list_files and read_file."
            )
        return ToolOutcome(
            tool="search_text",
            output=output,
            mutated=False,
        )

    def _write_file(self, arguments: dict[str, Any]) -> ToolOutcome:
        _reject_unknown(
            arguments,
            {"path", "content", "content_lines", "expected_sha256"},
        )
        relative = _required_string(arguments, "path")
        content, input_mode = _text_or_lines(
            arguments,
            text_field="content",
            lines_field="content_lines",
            allow_empty=True,
        )
        expected = arguments.get("expected_sha256")
        if expected is not None and not isinstance(expected, str):
            raise ToolError("write_file.expected_sha256 must be a string or null")
        path = self._write_path(relative)
        existed = path.exists()
        if existed:
            current = self._read_text(path)
            _require_expected_hash(current, expected)
            if current == content:
                raise ToolError("write_file content is unchanged")
        elif expected is not None:
            raise ToolError("new files require expected_sha256 to be null")
        self._check_content_size(content)
        _write_workspace_text_atomic(path, content)
        return ToolOutcome(
            tool="write_file",
            output={
                "path": relative,
                "created": not existed,
                "input_mode": input_mode,
                "sha256": _sha256_bytes(content.encode("utf-8")),
                "bytes": len(content.encode("utf-8")),
            },
            mutated=True,
        )

    def _replace_text(self, arguments: dict[str, Any]) -> ToolOutcome:
        _reject_unknown(
            arguments,
            {"path", "old", "new", "old_lines", "new_lines", "expected_sha256"},
        )
        relative = _required_string(arguments, "path")
        uses_text = "old" in arguments or "new" in arguments
        uses_lines = "old_lines" in arguments or "new_lines" in arguments
        if uses_text == uses_lines:
            raise ToolError(
                "replace_text requires exactly one of old/new or old_lines/new_lines"
            )
        if uses_lines:
            old = "\n".join(_required_string_list(arguments, "old_lines"))
            new = "\n".join(
                _required_string_list(arguments, "new_lines", allow_empty=True)
            )
            input_mode = "lines"
            if not old:
                raise ToolError("old_lines must encode non-empty text")
        else:
            old = _required_string(arguments, "old")
            new = _required_string(arguments, "new", allow_empty=True)
            input_mode = "text"
        expected = _required_string(arguments, "expected_sha256")
        path = self._write_path(relative)
        if not path.is_file():
            raise ToolError(f"replace_text target does not exist: {relative}")
        current = self._read_text(path)
        _require_expected_hash(current, expected)
        occurrences = current.count(old)
        if occurrences != 1:
            raise ToolError(
                f"replace_text.old must occur exactly once; found {occurrences}"
            )
        updated = current.replace(old, new, 1)
        if updated == current:
            raise ToolError("replace_text replacement would not change file")
        self._check_content_size(updated)
        _write_workspace_text_atomic(path, updated)
        return ToolOutcome(
            tool="replace_text",
            output={
                "path": relative,
                "input_mode": input_mode,
                "sha256": _sha256_bytes(updated.encode("utf-8")),
                "bytes": len(updated.encode("utf-8")),
            },
            mutated=True,
        )

    def _delete_file(self, arguments: dict[str, Any]) -> ToolOutcome:
        _reject_unknown(arguments, {"path", "expected_sha256"})
        relative = _required_string(arguments, "path")
        expected = _required_string(arguments, "expected_sha256")
        path = self._write_path(relative)
        if not path.is_file():
            raise ToolError(f"delete_file target does not exist: {relative}")
        current = self._read_text(path)
        _require_expected_hash(current, expected)
        path.unlink()
        _fsync_directory(path.parent)
        return ToolOutcome(
            tool="delete_file",
            output={"path": relative, "deleted": True},
            mutated=True,
        )

    def _tracked_and_untracked_files(self) -> list[str]:
        completed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=self.workspace,
            capture_output=True,
            timeout=15,
            check=False,
        )
        if completed.returncode != 0:
            raise ToolError(
                completed.stderr.decode("utf-8", errors="replace").strip()
                or "git ls-files failed"
            )
        return sorted(
            item.decode("utf-8")
            for item in completed.stdout.split(b"\0")
            if item
        )

    def _read_path(self, relative: str, *, require_file: bool = True) -> Path:
        _reject_protected_path(relative)
        try:
            path = contained_path(self.workspace, relative, require_relative=True)
        except PathBoundaryError as exc:
            raise ToolError(str(exc)) from exc
        if require_file and not path.is_file():
            raise ToolError(f"file does not exist: {relative}")
        return path

    def _write_path(self, relative: str) -> Path:
        _reject_protected_path(relative)
        candidate = Path(relative)
        if candidate.is_absolute():
            raise ToolError(f"path must be relative to workspace: {relative}")
        lexical = self.workspace / candidate
        try:
            resolved = contained_path(
                self.workspace,
                relative,
                require_relative=True,
            )
        except PathBoundaryError as exc:
            raise ToolError(str(exc)) from exc
        if resolved in self.protected_write_paths:
            raise ToolError(f"path is protected from model writes: {relative}")
        current = self.workspace
        for part in candidate.parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise ToolError(f"write path traverses a symlink: {relative}")
        if lexical.is_symlink():
            raise ToolError(f"write target must not be a symlink: {relative}")
        lexical.parent.mkdir(parents=True, exist_ok=True)
        return lexical

    def _read_text(self, path: Path) -> str:
        size = path.stat().st_size
        if size > self.max_file_bytes:
            raise ToolError(
                f"file exceeds {self.max_file_bytes} byte limit: "
                f"{path.relative_to(self.workspace)}"
            )
        raw = path.read_bytes()
        if b"\0" in raw:
            raise ToolError(f"binary files are not supported: {path.name}")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"file is not UTF-8: {path.name}") from exc

    def _check_content_size(self, content: str) -> None:
        if "\0" in content:
            raise ToolError("binary content is not supported")
        size = len(content.encode("utf-8"))
        if size > self.max_file_bytes:
            raise ToolError(f"content exceeds {self.max_file_bytes} byte limit")


def _reject_unknown(arguments: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(arguments).difference(allowed))
    if unknown:
        raise ToolError(f"tool arguments contain unknown fields: {', '.join(unknown)}")


def _normalize_scope_path(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return ""
    normalized = Path(stripped).as_posix().rstrip("/")
    if normalized in {"", "."}:
        return ""
    return normalized


def _required_string(
    arguments: dict[str, Any],
    field: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or (not allow_empty and not value):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise ToolError(f"{field} must be {qualifier}")
    return value


def _required_string_list(
    arguments: dict[str, Any],
    field: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    value = arguments.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ToolError(f"{field} must be an array of strings")
    if not allow_empty and not value:
        raise ToolError(f"{field} must be a non-empty array of strings")
    lines: list[str] = []
    for item in value:
        lines.extend(item.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    return lines


def _text_or_lines(
    arguments: dict[str, Any],
    *,
    text_field: str,
    lines_field: str,
    allow_empty: bool,
) -> tuple[str, str]:
    has_text = text_field in arguments
    has_lines = lines_field in arguments
    if has_text == has_lines:
        raise ToolError(
            f"exactly one of {text_field} or {lines_field} must be provided"
        )
    if has_lines:
        lines = _required_string_list(
            arguments,
            lines_field,
            allow_empty=allow_empty,
        )
        return "\n".join(lines), "lines"
    return _required_string(
        arguments,
        text_field,
        allow_empty=allow_empty,
    ), "text"


def _optional_positive_int(
    value: object,
    field: str,
    default: int | None,
) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ToolError(f"{field} must be a positive integer")
    return value


def _reject_protected_path(relative: str) -> None:
    candidate = Path(relative)
    if candidate.is_absolute():
        raise ToolError(f"path must be relative to workspace: {relative}")
    if ".git" in candidate.parts or ".sisyphus-harness" in candidate.parts:
        raise ToolError(f"path is protected: {relative}")


def _require_expected_hash(content: str, expected: str | None) -> None:
    actual = _sha256_bytes(content.encode("utf-8"))
    if expected is None:
        raise ToolError("existing files require expected_sha256")
    if expected != actual:
        raise ToolError(f"stale file hash: expected {expected}, current {actual}")


def _sha256_bytes(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _truncate_text(content: str, limit: int) -> tuple[str, bool]:
    if len(content) <= limit:
        return content, False
    return content[:limit], True


def _truncate_lines(lines: list[str], limit: int) -> tuple[list[str], bool]:
    result: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > limit:
            return result, True
        result.append(line)
        size += len(line) + 1
    return result, False


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_workspace_text_atomic(path: Path, content: str) -> None:
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
        _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
