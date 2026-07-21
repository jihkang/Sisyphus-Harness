from __future__ import annotations

from dataclasses import dataclass

from ..agent import AgentResult
from ..codec import WireModel, strict_object
from ..workspace import WorkspaceBundleRef


@dataclass(frozen=True, slots=True)
class CodingJobResult(WireModel):
    """Legacy v1 worker result retained for import and wire compatibility."""

    job_id: str
    attempt: int
    attempt_id: str
    success: bool
    source_bundle: WorkspaceBundleRef
    output_bundle: WorkspaceBundleRef
    agent_result: AgentResult
    schema_version: str = "sisyphus_harness.coding_job_result.v1"

    def __post_init__(self) -> None:
        if not self.job_id or not self.attempt_id:
            raise ValueError("coding job result identity must be non-empty")
        if (
            isinstance(self.attempt, bool)
            or not isinstance(self.attempt, int)
            or self.attempt <= 0
        ):
            raise ValueError("coding job result attempt must be positive")
        if self.attempt_id != f"{self.job_id}/attempt-{self.attempt:04d}":
            raise ValueError("coding job result attempt ID is inconsistent")
        if not isinstance(self.success, bool):
            raise ValueError("coding job result success must be a boolean")
        if type(self.source_bundle) is not WorkspaceBundleRef:
            raise TypeError(
                "coding job result source bundle must be an exact WorkspaceBundleRef"
            )
        if type(self.output_bundle) is not WorkspaceBundleRef:
            raise TypeError(
                "coding job result output bundle must be an exact WorkspaceBundleRef"
            )
        if type(self.agent_result) is not AgentResult:
            raise TypeError(
                "coding job result agent result must be an exact AgentResult"
            )
        if self.success != self.agent_result.success:
            raise ValueError("coding job result success is inconsistent")
        if self.schema_version != "sisyphus_harness.coding_job_result.v1":
            raise ValueError("unsupported coding job result schema")

    @classmethod
    def from_dict(cls, raw: object) -> CodingJobResult:
        raw = strict_object(
            raw,
            required={
                "job_id",
                "attempt",
                "attempt_id",
                "success",
                "source_bundle",
                "output_bundle",
                "agent_result",
                "schema_version",
            },
            label="coding job result",
        )
        strings = {
            key: raw[key]
            for key in ("job_id", "attempt_id", "schema_version")
        }
        if any(not isinstance(value, str) or not value for value in strings.values()):
            raise ValueError("coding job result string fields are invalid")
        attempt = raw["attempt"]
        if isinstance(attempt, bool) or not isinstance(attempt, int):
            raise ValueError("coding job result attempt must be an integer")
        success = raw["success"]
        if not isinstance(success, bool):
            raise ValueError("coding job result success must be a boolean")
        return cls(
            job_id=strings["job_id"],
            attempt=attempt,
            attempt_id=strings["attempt_id"],
            success=success,
            source_bundle=WorkspaceBundleRef.from_dict(raw["source_bundle"]),
            output_bundle=WorkspaceBundleRef.from_dict(raw["output_bundle"]),
            agent_result=AgentResult.from_dict(raw["agent_result"]),
            schema_version=strings["schema_version"],
        )
