from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import subprocess
from typing import Any

from .workspace_tool_arguments import (
    normalize_scope_path,
    optional_positive_int,
    reject_unknown,
    required_string,
)
from .workspace_tool_contracts import ToolError, ToolOutcome
from .workspace_tool_io import (
    BoundedWorkspaceIO,
    sha256_bytes,
    truncate_lines,
    truncate_text,
)
from .workspace_tool_paths import WorkspaceToolPathPolicy


class WorkspaceToolQueries:
    def __init__(
        self,
        workspace: Path,
        *,
        max_output_chars: int,
        timeout: Callable[[float], float],
        paths: WorkspaceToolPathPolicy,
        file_io: BoundedWorkspaceIO,
    ) -> None:
        self.workspace = workspace
        self.max_output_chars = max_output_chars
        self.timeout = timeout
        self.paths = paths
        self.file_io = file_io

    def list_files(self, arguments: dict[str, Any]) -> ToolOutcome:
        reject_unknown(arguments, {"prefix"})
        prefix = arguments.get("prefix", "")
        if not isinstance(prefix, str):
            raise ToolError("list_files.prefix must be a string")
        normalized_prefix = normalize_scope_path(prefix)
        if normalized_prefix:
            self.paths.read_path(normalized_prefix, require_file=False)
        files = self._tracked_and_untracked_files()
        if normalized_prefix:
            files = [
                path
                for path in files
                if path == normalized_prefix or path.startswith(f"{normalized_prefix}/")
            ]
        rendered, truncated = truncate_lines(files, self.max_output_chars)
        return ToolOutcome(
            tool="list_files",
            output={
                "files": rendered,
                "total_count": len(files),
                "truncated": truncated,
            },
            mutated=False,
        )

    def read_file(self, arguments: dict[str, Any]) -> ToolOutcome:
        reject_unknown(arguments, {"path", "start_line", "end_line"})
        relative = required_string(arguments, "path")
        path = self.paths.read_path(relative)
        start_line = optional_positive_int(
            arguments.get("start_line"),
            "start_line",
            1,
        )
        end_line = optional_positive_int(
            arguments.get("end_line"),
            "end_line",
            None,
        )
        assert start_line is not None
        if end_line is not None and end_line < start_line:
            raise ToolError(
                "read_file.end_line must be greater than or equal to start_line"
            )
        content = self.file_io.read_text(path)
        lines = content.splitlines(keepends=True)
        selected = "".join(lines[start_line - 1 : end_line])
        selected, truncated = truncate_text(selected, self.max_output_chars)
        return ToolOutcome(
            tool="read_file",
            output={
                "path": relative,
                "sha256": sha256_bytes(content.encode("utf-8")),
                "start_line": start_line,
                "end_line": min(end_line or len(lines), len(lines)),
                "total_lines": len(lines),
                "content": selected,
                "truncated": truncated,
            },
            mutated=False,
        )

    def search_text(self, arguments: dict[str, Any]) -> ToolOutcome:
        reject_unknown(arguments, {"query", "path", "max_results"})
        query = required_string(arguments, "query")
        if len(query) > 512:
            raise ToolError("search_text.query must be at most 512 characters")
        raw_path = arguments.get("path", "")
        if not isinstance(raw_path, str):
            raise ToolError("search_text.path must be a string")
        prefix = normalize_scope_path(raw_path)
        max_results = optional_positive_int(
            arguments.get("max_results"),
            "max_results",
            50,
        )
        assert max_results is not None
        max_results = min(max_results, 200)
        files = self._tracked_and_untracked_files()
        if prefix:
            self.paths.read_path(prefix, require_file=False)
            files = [
                path
                for path in files
                if path == prefix or path.startswith(f"{prefix}/")
            ]
        matches: list[dict[str, object]] = []
        skipped: list[str] = []
        rendered_size = 0
        for relative in files:
            try:
                path = self.paths.read_path(relative)
                content = self.file_io.read_text(path)
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

    def _tracked_and_untracked_files(self) -> list[str]:
        completed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=self.workspace,
            capture_output=True,
            timeout=self.timeout(15),
            check=False,
        )
        if completed.returncode != 0:
            raise ToolError(
                completed.stderr.decode("utf-8", errors="replace").strip()
                or "git ls-files failed"
            )
        return sorted(
            item.decode("utf-8") for item in completed.stdout.split(b"\0") if item
        )
