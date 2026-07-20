from __future__ import annotations

from dataclasses import dataclass

from .artifacts import ArtifactRef
from .codec import WireModel, strict_object


@dataclass(frozen=True, slots=True)
class AgentTask(WireModel):
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

    @classmethod
    def from_dict(cls, raw: object) -> AgentTask:
        raw = strict_object(
            raw,
            required={"instruction", "acceptance_criteria"},
            label="agent task",
        )
        instruction = raw["instruction"]
        criteria = raw["acceptance_criteria"]
        if not isinstance(instruction, str):
            raise ValueError("agent task instruction must be a string")
        if not isinstance(criteria, list) or any(
            not isinstance(criterion, str) for criterion in criteria
        ):
            raise ValueError("agent task acceptance criteria must be a string list")
        return cls(instruction=instruction, acceptance_criteria=tuple(criteria))


@dataclass(frozen=True, slots=True)
class AgentResult(WireModel):
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
    verification_artifacts: tuple[ArtifactRef, ...] = ()
    summary: str | None = None
    schema_version: str = "sisyphus_harness.agent_run.v2"

    def __post_init__(self) -> None:
        if self.schema_version not in {
            "sisyphus_harness.agent_run.v1",
            "sisyphus_harness.agent_run.v2",
        }:
            raise ValueError("unsupported agent result schema")
        if not self.run_id or not self.reason or not self.artifact_path:
            raise ValueError("agent result identity fields must be non-empty")
        for label, value in (
            ("steps", self.steps),
            ("compactions", self.compactions),
            ("verifications", self.verifications),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"agent result {label} must be non-negative")
        if self.schema_version == "sisyphus_harness.agent_run.v1" and (
            self.verification_artifacts
        ):
            raise ValueError("v1 agent results cannot contain verification artifacts")

    @classmethod
    def from_dict(cls, raw: object) -> AgentResult:
        if not isinstance(raw, dict):
            raise ValueError("agent result must be an object")
        schema = raw.get("schema_version")
        common = {
            "run_id",
            "success",
            "reason",
            "steps",
            "compactions",
            "verifications",
            "workspace_state_before",
            "workspace_state_after",
            "changed_paths",
            "artifact_path",
            "summary",
            "schema_version",
        }
        if schema == "sisyphus_harness.agent_run.v1":
            required = common
        elif schema == "sisyphus_harness.agent_run.v2":
            required = common | {"verification_artifacts"}
        else:
            raise ValueError("unsupported agent result schema")
        raw = strict_object(raw, required=required, label="agent result")
        string_fields = {
            key: raw[key]
            for key in (
                "run_id",
                "reason",
                "workspace_state_before",
                "workspace_state_after",
                "artifact_path",
                "schema_version",
            )
        }
        if any(
            not isinstance(value, str) or not value
            for value in string_fields.values()
        ):
            raise ValueError("agent result string fields are invalid")
        success = raw["success"]
        if not isinstance(success, bool):
            raise ValueError("agent result success must be a boolean")
        integers: dict[str, int] = {}
        for key in ("steps", "compactions", "verifications"):
            value = raw[key]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"agent result {key} must be an integer")
            integers[key] = value
        changed_paths = raw["changed_paths"]
        if not isinstance(changed_paths, list) or any(
            not isinstance(path, str) for path in changed_paths
        ):
            raise ValueError("agent result changed_paths must be a string list")
        artifacts_raw = raw.get("verification_artifacts", [])
        if not isinstance(artifacts_raw, list):
            raise ValueError("agent result verification_artifacts must be a list")
        summary = raw["summary"]
        if summary is not None and not isinstance(summary, str):
            raise ValueError("agent result summary must be a string or null")
        return cls(
            run_id=string_fields["run_id"],
            success=success,
            reason=string_fields["reason"],
            steps=integers["steps"],
            compactions=integers["compactions"],
            verifications=integers["verifications"],
            workspace_state_before=string_fields["workspace_state_before"],
            workspace_state_after=string_fields["workspace_state_after"],
            changed_paths=tuple(changed_paths),
            artifact_path=string_fields["artifact_path"],
            verification_artifacts=tuple(
                ArtifactRef.from_dict(item) for item in artifacts_raw
            ),
            summary=summary,
            schema_version=string_fields["schema_version"],
        )
