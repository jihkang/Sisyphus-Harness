from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..contracts.agent import AgentResult, AgentTask
from ..contracts.policy import CandidatePolicy
from ..contracts.verification import CommandSpec


@runtime_checkable
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


@runtime_checkable
class AgentRunFactoryPort(Protocol):
    def create(
        self,
        *,
        policy: CandidatePolicy,
        agent_artifact_root: Path,
        verification_artifact_root: Path,
    ) -> AgentRunPort:
        ...
