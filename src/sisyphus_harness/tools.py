from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from .deadline import DeadlineExceeded, MonotonicDeadline
from .workspace import PathBoundaryError
from .workspace_tool_contracts import ToolError, ToolOutcome
from .workspace_tool_io import BoundedWorkspaceIO
from .workspace_tool_mutations import WorkspaceToolMutations
from .workspace_tool_paths import WorkspaceToolPathPolicy
from .workspace_tool_queries import WorkspaceToolQueries


class WorkspaceTools:
    def __init__(
        self,
        workspace: Path,
        *,
        max_file_bytes: int,
        max_output_chars: int,
        protected_write_paths: tuple[Path, ...] = (),
        allowed_write_paths: tuple[Path, ...] | None = None,
        deadline: MonotonicDeadline | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.max_file_bytes = max_file_bytes
        self.max_output_chars = max_output_chars
        self.deadline = deadline
        try:
            paths = WorkspaceToolPathPolicy(
                self.workspace,
                protected_write_paths=protected_write_paths,
                allowed_write_paths=allowed_write_paths,
                timeout=self._timeout,
            )
        except PathBoundaryError as exc:
            raise ToolError(f"write policy path is outside workspace: {exc}") from exc
        self.protected_write_paths = paths.protected_write_paths
        self.allowed_write_paths = paths.allowed_write_paths
        file_io = BoundedWorkspaceIO(
            self.workspace,
            max_file_bytes=self.max_file_bytes,
        )
        self._queries = WorkspaceToolQueries(
            self.workspace,
            max_output_chars=self.max_output_chars,
            timeout=self._timeout,
            paths=paths,
            file_io=file_io,
        )
        self._mutations = WorkspaceToolMutations(paths=paths, file_io=file_io)

    def execute(self, tool: str, arguments: dict[str, Any]) -> ToolOutcome:
        handlers = {
            "list_files": self._queries.list_files,
            "read_file": self._queries.read_file,
            "search_text": self._queries.search_text,
            "write_file": self._mutations.write_file,
            "replace_text": self._mutations.replace_text,
            "delete_file": self._mutations.delete_file,
        }
        handler = handlers.get(tool)
        if handler is None:
            raise ToolError(f"unsupported tool: {tool}")
        try:
            self._require_time()
            return handler(arguments)
        except ToolError:
            raise
        except DeadlineExceeded as exc:
            raise ToolError(str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"{tool} operation timed out") from exc
        except (OSError, UnicodeError) as exc:
            detail = exc.strerror if isinstance(exc, OSError) else str(exc)
            raise ToolError(f"{tool} filesystem operation failed: {detail}") from exc

    def _require_time(self) -> None:
        if self.deadline is not None:
            self.deadline.remaining()

    def _timeout(self, maximum: float) -> float:
        if self.deadline is None:
            return maximum
        return self.deadline.bounded_timeout(maximum)


__all__ = ["ToolError", "ToolOutcome", "WorkspaceTools"]
