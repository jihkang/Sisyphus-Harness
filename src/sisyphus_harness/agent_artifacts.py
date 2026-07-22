from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
from pathlib import Path
from typing import Any

from .agent_state import AgentRunState, AgentTermination
from .config import AgentLimits
from .contracts.agent import AgentResult, AgentTask
from .contracts.policy import CadencePolicy
from .contracts.workspace import WorkspaceSnapshot
from .protocol import AgentDecision
from .provider import ChatMessage
from .run_store import AgentRunStore
from .workspace import snapshot_workspace


@dataclass(frozen=True, slots=True)
class AgentTurn:
    step: int
    before: WorkspaceSnapshot
    messages: tuple[ChatMessage, ...]
    model_content: str
    model_duration_ms: int
    decision: AgentDecision | None
    prompt_tokens: int | None
    completion_tokens: int | None


@dataclass(frozen=True, slots=True)
class AgentRunRecorder:
    store: AgentRunStore
    run_id: str
    initial: WorkspaceSnapshot
    workspace: Path

    def write_metadata(
        self,
        *,
        task: AgentTask,
        limits: AgentLimits,
        cadence: CadencePolicy,
        strategy_prompt: str,
        protected_write_paths: tuple[Path, ...],
        allowed_write_paths: tuple[Path, ...] | None,
    ) -> None:
        self.store.write_metadata(
            {
                "schema_version": "sisyphus_harness.agent_metadata.v1",
                "run_id": self.run_id,
                "workspace": str(self.workspace),
                "task": {
                    "instruction": task.instruction,
                    "acceptance_criteria": list(task.acceptance_criteria),
                },
                "limits": asdict(limits),
                "cadence": cadence.to_dict(),
                "strategy_prompt_sha256": _sha256_text(strategy_prompt),
                "protected_write_paths": [
                    str(path) for path in protected_write_paths
                ],
                "allowed_write_paths": (
                    None
                    if allowed_write_paths is None
                    else [str(path) for path in allowed_write_paths]
                ),
                "started_at": _utc_now(),
                "workspace_snapshot": self.initial.to_dict(),
            }
        )

    def write_step(
        self,
        turn: AgentTurn,
        *,
        after: WorkspaceSnapshot,
        event: dict[str, Any],
    ) -> None:
        self.store.write_step(
            turn.step,
            {
                "schema_version": "sisyphus_harness.agent_step.v1",
                "step": turn.step,
                "started_at": _utc_now(),
                "workspace_before": turn.before.to_dict(),
                "workspace_after": after.to_dict(),
                "workspace_changed": turn.before.state_hash != after.state_hash,
                "messages": [message.to_dict() for message in turn.messages],
                "model_response": turn.model_content,
                "model_duration_ms": turn.model_duration_ms,
                "prompt_tokens": turn.prompt_tokens,
                "completion_tokens": turn.completion_tokens,
                "decision": (
                    turn.decision.to_dict() if turn.decision is not None else None
                ),
                "event": event,
            },
        )

    def write_compaction(
        self,
        index: int,
        payload: dict[str, object],
    ) -> None:
        self.store.write_compaction(index, payload)

    def finish(
        self,
        state: AgentRunState,
        termination: AgentTermination,
    ) -> AgentResult:
        final = snapshot_workspace(self.workspace)
        result = AgentResult(
            run_id=self.run_id,
            success=termination.success,
            reason=termination.reason,
            steps=termination.steps,
            compactions=state.compactions,
            verifications=state.verifications,
            workspace_state_before=self.initial.state_hash,
            workspace_state_after=final.state_hash,
            changed_paths=final.changed_paths,
            artifact_path=str(self.store.root),
            verification_artifacts=tuple(state.verification_artifacts),
            summary=state.final_summary,
        )
        payload = result.to_dict()
        payload["finished_at"] = _utc_now()
        self.store.write_final(payload)
        return result


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
