from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..contracts.agent import AgentResult, AgentTask
from ..contracts.verification import CommandSpec


class AgentRunPort(Protocol):
    def run(
        self,
        workspace: Path,
        task: AgentTask,
        verification_commands: tuple[CommandSpec, ...],
        *,
        run_id: str | None = None,
    ) -> AgentResult:
        ...
