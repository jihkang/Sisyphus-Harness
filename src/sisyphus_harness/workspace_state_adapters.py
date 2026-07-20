from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .contracts.workspace import WorkspaceSnapshot
from .infra.workspace_bundle import workspace_tree_hash
from .workspace import snapshot_workspace


@dataclass(frozen=True, slots=True)
class GitWorkspaceStateAdapter:
    def snapshot(self, workspace: Path) -> WorkspaceSnapshot:
        return snapshot_workspace(workspace)


@dataclass(frozen=True, slots=True)
class TreeHashWorkspaceStateAdapter:
    source_commit_sha: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", self.source_commit_sha) is None:
            raise ValueError("tree workspace source commit must be a full SHA")

    def snapshot(self, workspace: Path) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(
            commit_sha=self.source_commit_sha,
            state_hash=workspace_tree_hash(workspace),
            changed_paths=(),
        )
