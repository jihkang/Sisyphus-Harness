from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..agent import LocalCodingAgent
from ..config import AgentLimits
from ..contracts.agent import AgentResult, AgentTask
from ..contracts.policy import CandidatePolicy
from ..contracts.verification import CommandSpec, VerificationReceipt
from ..ports.agent_run import AgentRunPort
from ..ports.verification import VerificationPort
from ..provider import ChatProvider
from ..verifier import BoundedVerifier


@dataclass(slots=True)
class InProcessVerificationAdapter:
    delegate: VerificationPort

    @classmethod
    def from_artifact_root(cls, artifact_root: Path) -> InProcessVerificationAdapter:
        return cls(BoundedVerifier(artifact_root))

    def verify(
        self,
        workspace: Path,
        commands: tuple[CommandSpec, ...],
        *,
        run_id: str | None = None,
    ) -> VerificationReceipt:
        return self.delegate.verify(workspace, commands, run_id=run_id)


@dataclass(slots=True)
class InProcessAgentRunAdapter:
    delegate: AgentRunPort

    def run(
        self,
        workspace: Path,
        task: AgentTask,
        verification_commands: tuple[CommandSpec, ...],
        *,
        run_id: str | None = None,
    ) -> AgentResult:
        return self.delegate.run(
            workspace,
            task,
            verification_commands,
            run_id=run_id,
        )


@dataclass(frozen=True, slots=True)
class InProcessAgentRunFactory:
    provider: ChatProvider
    limits: AgentLimits
    protected_write_paths: tuple[Path, ...] = ()

    def create(
        self,
        *,
        policy: CandidatePolicy,
        agent_artifact_root: Path,
        verification_artifact_root: Path,
    ) -> AgentRunPort:
        verifier = InProcessVerificationAdapter.from_artifact_root(
            verification_artifact_root
        )
        return InProcessAgentRunAdapter(
            LocalCodingAgent(
                provider=self.provider,
                verifier=verifier,
                agent_artifact_root=agent_artifact_root,
                limits=self.limits,
                cadence=policy.cadence,
                strategy_prompt=policy.strategy_prompt,
                protected_write_paths=self.protected_write_paths,
            )
        )
