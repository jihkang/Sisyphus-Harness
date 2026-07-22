from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import subprocess

from .workspace import PathBoundaryError, contained_path
from .workspace_tool_contracts import ToolError


class WorkspaceToolPathPolicy:
    def __init__(
        self,
        workspace: Path,
        *,
        protected_write_paths: tuple[Path, ...],
        allowed_write_paths: tuple[Path, ...] | None,
        timeout: Callable[[float], float],
    ) -> None:
        self.workspace = workspace
        self.timeout = timeout
        self.protected_write_paths = tuple(
            contained_path(self.workspace, path).resolve(strict=False)
            for path in protected_write_paths
        )
        self.allowed_write_paths = (
            None
            if allowed_write_paths is None
            else tuple(
                contained_path(
                    self.workspace,
                    path,
                    require_relative=not Path(path).is_absolute(),
                ).resolve(strict=False)
                for path in allowed_write_paths
            )
        )

    def read_path(self, relative: str, *, require_file: bool = True) -> Path:
        reject_protected_path(relative)
        try:
            path = contained_path(self.workspace, relative, require_relative=True)
        except PathBoundaryError as exc:
            raise ToolError(str(exc)) from exc
        reject_resolved_protected_path(self.workspace, path, relative)
        if require_file and not path.is_file():
            raise ToolError(f"file does not exist: {relative}")
        return path

    def write_path(self, relative: str) -> Path:
        reject_protected_path(relative)
        candidate = Path(relative)
        if candidate.name.casefold() == ".gitignore":
            raise ToolError(
                f"Git ignore controls are protected from model writes: {relative}"
            )
        lexical = self.workspace / candidate
        try:
            resolved = contained_path(
                self.workspace,
                relative,
                require_relative=True,
            )
        except PathBoundaryError as exc:
            raise ToolError(str(exc)) from exc
        resolved_target = resolved.resolve(strict=False)
        if any(
            resolved_target == protected or protected in resolved_target.parents
            for protected in self.protected_write_paths
        ):
            raise ToolError(f"path is protected from model writes: {relative}")
        if self.allowed_write_paths is not None and not any(
            resolved_target == allowed or allowed in resolved_target.parents
            for allowed in self.allowed_write_paths
        ):
            raise ToolError(f"path is outside the model write allowlist: {relative}")
        current = self.workspace
        for part in candidate.parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise ToolError(f"write path traverses a symlink: {relative}")
        if lexical.is_symlink():
            raise ToolError(f"write target must not be a symlink: {relative}")
        self._reject_ignored_write(relative)
        lexical.parent.mkdir(parents=True, exist_ok=True)
        return lexical

    def _reject_ignored_write(self, relative: str) -> None:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", relative],
            cwd=self.workspace,
            capture_output=True,
            timeout=self.timeout(15),
            check=False,
        )
        if tracked.returncode == 0:
            return
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", relative],
            cwd=self.workspace,
            capture_output=True,
            timeout=self.timeout(15),
            check=False,
        )
        if ignored.returncode == 0:
            raise ToolError(
                f"model writes to Git-ignored paths are not observable: {relative}"
            )
        if ignored.returncode != 1:
            detail = ignored.stderr.decode("utf-8", errors="replace").strip()
            raise ToolError(detail or "git check-ignore failed")


def reject_protected_path(relative: str) -> None:
    candidate = Path(relative)
    if (
        candidate.is_absolute()
        or "\\" in relative
        or candidate.as_posix() != relative
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ToolError(f"path must be relative to workspace: {relative}")
    if ".git" in candidate.parts or ".sisyphus-harness" in candidate.parts:
        raise ToolError(f"path is protected: {relative}")


def reject_resolved_protected_path(
    workspace: Path,
    resolved: Path,
    requested: str,
) -> None:
    for protected in (workspace / ".git", workspace / ".sisyphus-harness"):
        protected_root = protected.resolve(strict=False)
        try:
            resolved.relative_to(protected_root)
        except ValueError:
            continue
        raise ToolError(f"path resolves into protected state: {requested}")
