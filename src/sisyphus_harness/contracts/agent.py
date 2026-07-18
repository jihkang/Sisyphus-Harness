from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentTask:
    instruction: str
    acceptance_criteria: tuple[str, ...]

    def __post_init__(self) -> None:
        instruction = self.instruction.strip()
        criteria = tuple(criterion.strip() for criterion in self.acceptance_criteria)
        if not instruction:
            raise ValueError("agent task instruction must be non-empty")
        if not criteria or any(not criterion for criterion in criteria):
            raise ValueError("agent task requires acceptance criteria")
        if len(set(criteria)) != len(criteria):
            raise ValueError("agent task acceptance criteria must be unique")
        object.__setattr__(self, "instruction", instruction)
        object.__setattr__(self, "acceptance_criteria", criteria)


@dataclass(frozen=True, slots=True)
class AgentResult:
    run_id: str
    success: bool
    reason: str
    steps: int
    compactions: int
    verifications: int
    workspace_state_before: str
    workspace_state_after: str
    changed_paths: tuple[str, ...]
    artifact_path: str
    summary: str | None = None
    schema_version: str = "sisyphus_harness.agent_run.v1"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "success": self.success,
            "reason": self.reason,
            "steps": self.steps,
            "compactions": self.compactions,
            "verifications": self.verifications,
            "workspace_state_before": self.workspace_state_before,
            "workspace_state_after": self.workspace_state_after,
            "changed_paths": list(self.changed_paths),
            "artifact_path": self.artifact_path,
            "summary": self.summary,
        }
