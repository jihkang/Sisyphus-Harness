from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..contracts.workspace import WorkspaceSnapshot


@runtime_checkable
class WorkspaceStatePort(Protocol):
    def snapshot(self, workspace: Path) -> WorkspaceSnapshot:
        ...
