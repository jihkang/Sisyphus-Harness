from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from .compaction import compact_events, transcript_size
from .contracts.agent import AgentTask
from .contracts.artifacts import ArtifactRef
from .contracts.policy import CadencePolicy
from .contracts.workspace import WorkspaceSnapshot
from .protocol import AgentDecision


StateCriterionKey = tuple[str, tuple[tuple[str, str], ...]]


@dataclass(frozen=True, slots=True)
class AgentTermination:
    success: bool
    reason: str
    steps: int


@dataclass(slots=True)
class AgentRunState:
    events: list[dict[str, Any]] = field(default_factory=list)
    compact_summary: dict[str, Any] | None = None
    compactions: int = 0
    verifications: int = 0
    verification_artifacts: list[ArtifactRef] = field(default_factory=list)
    protocol_errors: int = 0
    mutations_since_verify: int = 0
    last_failed_verification_state: str | None = None
    known_file_hashes: dict[str, str] = field(default_factory=dict)
    working_file: dict[str, object] | None = None
    criterion_status: dict[str, str] = field(default_factory=dict)
    state_visits: dict[StateCriterionKey, int] = field(default_factory=dict)
    workspace_cycle_streak: int = 0
    last_fingerprint: str | None = None
    repeated_fingerprint: int = 0
    final_summary: str | None = None

    @classmethod
    def start(
        cls,
        task: AgentTask,
        initial: WorkspaceSnapshot,
    ) -> AgentRunState:
        criterion_status = {
            criterion: "not_run" for criterion in task.acceptance_criteria
        }
        return cls(
            criterion_status=criterion_status,
            state_visits={
                state_criterion_key(initial.state_hash, criterion_status): 0
            },
        )

    def should_compact(
        self,
        step: int,
        cadence: CadencePolicy,
        *,
        max_compactions: int,
    ) -> bool:
        if self.compactions >= max_compactions:
            return False
        if len(self.events) <= cadence.keep_recent_events:
            return False
        return (
            step % cadence.compaction_interval_steps == 0
            or transcript_size(self.events) > cadence.context_char_limit
        )

    def compact(self, step: int, cadence: CadencePolicy) -> dict[str, object]:
        self.compact_summary, self.events = compact_events(
            self.compact_summary,
            self.events,
            keep_recent=cadence.keep_recent_events,
        )
        self.compactions += 1
        return {
            "schema_version": "sisyphus_harness.compaction.v1",
            "step": step,
            "summary": self.compact_summary,
            "retained_events": self.events,
        }

    def observe_decision(
        self,
        decision: AgentDecision,
        *,
        stagnation_limit: int,
    ) -> tuple[str, bool]:
        fingerprint = decision_fingerprint(decision)
        if fingerprint == self.last_fingerprint:
            self.repeated_fingerprint += 1
        else:
            self.repeated_fingerprint = 1
            self.last_fingerprint = fingerprint
        return fingerprint, self.repeated_fingerprint >= stagnation_limit

    def remember_verification_state(
        self,
        state_hash: str,
        step: int,
    ) -> None:
        self.state_visits.setdefault(
            state_criterion_key(state_hash, self.criterion_status),
            step,
        )

    def update_criterion_status(
        self,
        verification_event: dict[str, Any],
    ) -> None:
        criteria = verification_event.get("criteria")
        if not isinstance(criteria, list):
            return
        for item in criteria:
            if not isinstance(item, dict):
                continue
            criterion = item.get("criterion")
            passed = item.get("passed")
            if isinstance(criterion, str) and isinstance(passed, bool):
                self.criterion_status[criterion] = "passed" if passed else "failed"

    def observe_workspace_state(
        self,
        state_hash: str,
        *,
        step: int,
        event: dict[str, Any],
        stagnation_limit: int,
    ) -> bool:
        state_key = state_criterion_key(state_hash, self.criterion_status)
        previous_step = self.state_visits.get(state_key)
        if previous_step is None:
            self.workspace_cycle_streak = 0
            self.state_visits[state_key] = step
            return False
        self.workspace_cycle_streak += 1
        event["workspace_cycle"] = {
            "detected": True,
            "previous_step": previous_step,
            "repeat_count": self.workspace_cycle_streak,
            "feedback": (
                "This mutation returned the workspace to an earlier state "
                "without changing criterion outcomes. Do not toggle or reorder "
                "the same implementation. Inspect the current file and make a "
                "criterion-specific semantic repair."
            ),
        }
        return self.workspace_cycle_streak >= stagnation_limit


def decision_fingerprint(decision: AgentDecision) -> str:
    payload: dict[str, object] = {"type": decision.kind}
    if decision.kind == "tool":
        payload["tool"] = decision.tool
        payload["arguments"] = decision.arguments
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def state_criterion_key(
    state_hash: str,
    criterion_status: dict[str, str],
) -> StateCriterionKey:
    return state_hash, tuple(sorted(criterion_status.items()))
