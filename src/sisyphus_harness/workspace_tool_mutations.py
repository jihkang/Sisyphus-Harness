from __future__ import annotations

from typing import Any

from .workspace_tool_arguments import (
    reject_unknown,
    required_string,
    required_string_list,
    text_or_lines,
)
from .workspace_tool_contracts import ToolError, ToolOutcome
from .workspace_tool_io import BoundedWorkspaceIO, require_expected_hash, sha256_bytes
from .workspace_tool_paths import WorkspaceToolPathPolicy


class WorkspaceToolMutations:
    def __init__(
        self,
        *,
        paths: WorkspaceToolPathPolicy,
        file_io: BoundedWorkspaceIO,
    ) -> None:
        self.paths = paths
        self.file_io = file_io

    def write_file(self, arguments: dict[str, Any]) -> ToolOutcome:
        reject_unknown(
            arguments,
            {"path", "content", "content_lines", "expected_sha256"},
        )
        relative = required_string(arguments, "path")
        content, input_mode = text_or_lines(
            arguments,
            text_field="content",
            lines_field="content_lines",
            allow_empty=True,
        )
        expected = arguments.get("expected_sha256")
        if expected is not None and not isinstance(expected, str):
            raise ToolError("write_file.expected_sha256 must be a string or null")
        path = self.paths.write_path(relative)
        existed = path.exists()
        if existed:
            current = self.file_io.read_text(path)
            require_expected_hash(current, expected)
            if current == content:
                raise ToolError("write_file content is unchanged")
        elif expected is not None:
            raise ToolError("new files require expected_sha256 to be null")
        self.file_io.check_content_size(content)
        self.file_io.write_text_atomic(path, content)
        return ToolOutcome(
            tool="write_file",
            output={
                "path": relative,
                "created": not existed,
                "input_mode": input_mode,
                "sha256": sha256_bytes(content.encode("utf-8")),
                "bytes": len(content.encode("utf-8")),
            },
            mutated=True,
        )

    def replace_text(self, arguments: dict[str, Any]) -> ToolOutcome:
        reject_unknown(
            arguments,
            {"path", "old", "new", "old_lines", "new_lines", "expected_sha256"},
        )
        relative = required_string(arguments, "path")
        uses_text = "old" in arguments or "new" in arguments
        uses_lines = "old_lines" in arguments or "new_lines" in arguments
        if uses_text == uses_lines:
            raise ToolError(
                "replace_text requires exactly one of old/new or old_lines/new_lines"
            )
        if uses_lines:
            old = "\n".join(required_string_list(arguments, "old_lines"))
            new = "\n".join(
                required_string_list(arguments, "new_lines", allow_empty=True)
            )
            input_mode = "lines"
            if not old:
                raise ToolError("old_lines must encode non-empty text")
        else:
            old = required_string(arguments, "old")
            new = required_string(arguments, "new", allow_empty=True)
            input_mode = "text"
        expected = required_string(arguments, "expected_sha256")
        path = self.paths.write_path(relative)
        if not path.is_file():
            raise ToolError(f"replace_text target does not exist: {relative}")
        current = self.file_io.read_text(path)
        require_expected_hash(current, expected)
        occurrences = current.count(old)
        if occurrences != 1:
            raise ToolError(
                f"replace_text.old must occur exactly once; found {occurrences}"
            )
        updated = current.replace(old, new, 1)
        if updated == current:
            raise ToolError("replace_text replacement would not change file")
        self.file_io.check_content_size(updated)
        self.file_io.write_text_atomic(path, updated)
        return ToolOutcome(
            tool="replace_text",
            output={
                "path": relative,
                "input_mode": input_mode,
                "sha256": sha256_bytes(updated.encode("utf-8")),
                "bytes": len(updated.encode("utf-8")),
            },
            mutated=True,
        )

    def delete_file(self, arguments: dict[str, Any]) -> ToolOutcome:
        reject_unknown(arguments, {"path", "expected_sha256"})
        relative = required_string(arguments, "path")
        expected = required_string(arguments, "expected_sha256")
        path = self.paths.write_path(relative)
        if not path.is_file():
            raise ToolError(f"delete_file target does not exist: {relative}")
        current = self.file_io.read_text(path)
        require_expected_hash(current, expected)
        self.file_io.delete_file(path)
        return ToolOutcome(
            tool="delete_file",
            output={"path": relative, "deleted": True},
            mutated=True,
        )
