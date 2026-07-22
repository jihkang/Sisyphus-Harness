from __future__ import annotations

from pathlib import Path
import time
from typing import Any
import uuid

from .agent_artifacts import AgentRunRecorder
from .agent_context import (
    AgentPromptRenderer,
    SAFETY_PROMPT as SAFETY_PROMPT,
    workspace_observation,
)
from .agent_loop import AgentRunLoop
from .agent_state import AgentRunState
from .agent_transitions import (
    AgentToolTransitionHandler,
    AgentVerificationTransitionHandler,
)
from .config import AgentLimits
from .contracts.agent import AgentResult, AgentTask
from .contracts.policy import CadencePolicy
from .contracts.verification import CommandSpec
from .deadline import MonotonicDeadline
from .ports.verification import VerificationPort
from .provider import ChatMessage, ChatProvider
from .run_store import AgentRunStore
from .tools import WorkspaceTools
from .workspace import snapshot_workspace


class LocalCodingAgent:
    def __init__(
        self,
        *,
        provider: ChatProvider,
        verifier: VerificationPort,
        agent_artifact_root: Path,
        limits: AgentLimits,
        cadence: CadencePolicy,
        strategy_prompt: str,
        protected_write_paths: tuple[Path, ...] = (),
        allowed_write_paths: tuple[Path, ...] | None = None,
    ) -> None:
        self.provider = provider
        self.verifier = verifier
        self.agent_artifact_root = agent_artifact_root
        self.limits = limits
        self.cadence = cadence
        self.strategy_prompt = strategy_prompt
        self.protected_write_paths = protected_write_paths
        self.allowed_write_paths = allowed_write_paths

    def run(
        self,
        workspace: Path,
        task: AgentTask,
        verification_commands: tuple[CommandSpec, ...],
        *,
        run_id: str | None = None,
    ) -> AgentResult:
        if not verification_commands:
            raise ValueError("agent run requires verification commands")
        _require_criterion_coverage(task, verification_commands)

        clock = time.monotonic
        deadline = MonotonicDeadline.after(
            self.limits.max_runtime_seconds,
            clock=clock,
        )
        root = workspace.resolve()
        initial = snapshot_workspace(root)
        resolved_run_id = run_id or f"agent-{uuid.uuid4().hex}"
        recorder = AgentRunRecorder(
            store=AgentRunStore(self.agent_artifact_root, resolved_run_id),
            run_id=resolved_run_id,
            initial=initial,
            workspace=root,
        )
        recorder.write_metadata(
            task=task,
            limits=self.limits,
            cadence=self.cadence,
            strategy_prompt=self.strategy_prompt,
            protected_write_paths=self.protected_write_paths,
            allowed_write_paths=self.allowed_write_paths,
        )
        workspace_tools = WorkspaceTools(
            root,
            max_file_bytes=self.limits.max_file_bytes,
            max_output_chars=self.limits.max_tool_output_chars,
            protected_write_paths=self.protected_write_paths,
            allowed_write_paths=self.allowed_write_paths,
            deadline=deadline,
        )
        state = AgentRunState.start(task, initial)
        loop = AgentRunLoop(
            provider=self.provider,
            task=task,
            limits=self.limits,
            cadence=self.cadence,
            workspace=root,
            deadline=deadline,
            renderer=AgentPromptRenderer(
                strategy_prompt=self.strategy_prompt,
                cadence=self.cadence,
            ),
            recorder=recorder,
            observe_workspace=lambda: workspace_observation(root, workspace_tools),
            tool_transitions=AgentToolTransitionHandler(
                tools=workspace_tools,
                workspace=root,
                stagnation_limit=self.cadence.stagnation_limit,
            ),
            verification_transitions=AgentVerificationTransitionHandler(
                verifier=self.verifier,
                workspace=root,
                commands=verification_commands,
                run_id=resolved_run_id,
                deadline_monotonic=deadline.expires_at,
                verification_interval_mutations=self.cadence.verification_interval_mutations,
            ),
            clock=clock,
        )
        return loop.run(state)

    def _messages(
        self,
        task: AgentTask,
        events: list[dict[str, Any]],
        compact_summary: dict[str, Any] | None,
        observation: dict[str, object] | None,
        reflection_due: bool,
        known_file_hashes: dict[str, str],
        working_file: dict[str, object] | None,
        criterion_status: dict[str, str],
    ) -> tuple[ChatMessage, ...]:
        return AgentPromptRenderer(
            strategy_prompt=self.strategy_prompt,
            cadence=self.cadence,
        ).messages(
            task,
            events,
            compact_summary,
            observation,
            reflection_due,
            known_file_hashes,
            working_file,
            criterion_status,
        )


def _require_criterion_coverage(
    task: AgentTask,
    commands: tuple[CommandSpec, ...],
) -> None:
    verified = {
        criterion.strip()
        for command in commands
        for criterion in command.criteria
    }
    missing = [
        criterion
        for criterion in task.acceptance_criteria
        if criterion not in verified
    ]
    if missing:
        raise ValueError(
            "verification commands do not cover acceptance criteria: "
            + ", ".join(missing)
        )
