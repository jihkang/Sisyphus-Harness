from __future__ import annotations

from dataclasses import dataclass

from ..agent import AgentResult
from ..codec import WireModel, sha256_digest, strict_object
from ..workspace import WorkspaceBundleRef
from ._validation import (
    digest,
    positive_integer,
    string,
    validate_attempt_identity,
)


@dataclass(frozen=True, slots=True)
class AttemptFinished(WireModel):
    """Immutable execution lineage published by a Worker.

    ``agent_result.success`` remains diagnostic model output. It is deliberately
    not projected into this envelope as task success; only Control may publish a
    semantic ``TaskOutcome``.
    """

    job_id: str
    attempt: int
    attempt_id: str
    source_bundle: WorkspaceBundleRef
    output_bundle: WorkspaceBundleRef
    agent_result: AgentResult
    schema_version: str = "sisyphus_harness.attempt_finished.v1"

    def __post_init__(self) -> None:
        validate_attempt_identity(self.job_id, self.attempt, self.attempt_id)
        if type(self.source_bundle) is not WorkspaceBundleRef:
            raise TypeError("attempt source bundle must be an exact WorkspaceBundleRef")
        if type(self.output_bundle) is not WorkspaceBundleRef:
            raise TypeError("attempt output bundle must be an exact WorkspaceBundleRef")
        if type(self.agent_result) is not AgentResult:
            raise TypeError("attempt agent result must be an exact AgentResult")
        if self.schema_version != "sisyphus_harness.attempt_finished.v1":
            raise ValueError("unsupported attempt-finished schema")

    def content_payload(self) -> dict[str, object]:
        return WireModel.to_dict(self)

    @property
    def attempt_digest(self) -> str:
        return sha256_digest(self.content_payload())

    def to_dict(self) -> dict[str, object]:
        payload = self.content_payload()
        payload["attempt_digest"] = self.attempt_digest
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> AttemptFinished:
        raw = strict_object(
            raw,
            required={
                "job_id",
                "attempt",
                "attempt_id",
                "source_bundle",
                "output_bundle",
                "agent_result",
                "schema_version",
                "attempt_digest",
            },
            label="attempt-finished result",
        )
        result = cls(
            job_id=string(raw["job_id"], "attempt job ID"),
            attempt=positive_integer(raw["attempt"], "attempt number"),
            attempt_id=string(raw["attempt_id"], "attempt ID"),
            source_bundle=WorkspaceBundleRef.from_dict(raw["source_bundle"]),
            output_bundle=WorkspaceBundleRef.from_dict(raw["output_bundle"]),
            agent_result=AgentResult.from_dict(raw["agent_result"]),
            schema_version=string(raw["schema_version"], "attempt schema"),
        )
        recorded = digest(raw["attempt_digest"], "attempt digest")
        if recorded != result.attempt_digest:
            raise ValueError("attempt digest does not match content")
        return result
