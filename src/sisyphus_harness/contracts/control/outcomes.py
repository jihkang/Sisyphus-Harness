from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..artifacts import ArtifactRef
from ..codec import WireModel, sha256_digest, strict_object
from ..evidence_contract import (
    ContractEvaluation,
    EvidenceContract,
    EvaluationLifecycle,
    LogicalResult,
)
from ..verification_service import VerificationProfile
from ._validation import (
    digest,
    positive_integer,
    string,
    validate_producer_authority,
    validate_attempt_identity,
)


class TaskOutcomeDecision(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"

    @classmethod
    def from_evaluation(
        cls,
        evaluation: ContractEvaluation,
    ) -> TaskOutcomeDecision:
        if type(evaluation) is not ContractEvaluation:
            raise TypeError("task outcome evaluation must be a ContractEvaluation")
        if evaluation.lifecycle is EvaluationLifecycle.ERROR:
            return cls.INDETERMINATE
        decisions = {
            LogicalResult.PASS: cls.PASSED,
            LogicalResult.FAIL: cls.FAILED,
            LogicalResult.INDETERMINATE: cls.INDETERMINATE,
        }
        try:
            return decisions[evaluation.logical_result]
        except KeyError as exc:
            raise ValueError(
                "completed task evaluation has no logical result"
            ) from exc


@dataclass(frozen=True, slots=True)
class TaskOutcome(WireModel):
    """Control-owned semantic result bound to one immutable attempt and receipt."""

    job_id: str
    attempt: int
    attempt_id: str
    attempt_digest: str
    source_bundle_id: str
    output_bundle_id: str
    contract_digest: str
    contract: EvidenceContract
    verification_run_id: str
    verification_request_digest: str
    verification_profile_digest: str
    verification_profile: VerificationProfile
    verification_receipt_digest: str
    verification_receipt_artifact: ArtifactRef
    producer_authority: str
    observation_set_digest: str
    evaluation_digest: str
    evaluation: ContractEvaluation
    decision: TaskOutcomeDecision
    evidence_finished_at: str
    schema_version: str = "sisyphus_harness.task_outcome.v1"

    def __post_init__(self) -> None:
        validate_attempt_identity(self.job_id, self.attempt, self.attempt_id)
        for value, label in (
            (self.attempt_digest, "task outcome attempt digest"),
            (self.contract_digest, "task outcome contract digest"),
            (
                self.verification_request_digest,
                "task outcome verification request digest",
            ),
            (
                self.verification_profile_digest,
                "task outcome verification profile digest",
            ),
            (
                self.verification_receipt_digest,
                "task outcome verification receipt digest",
            ),
            (
                self.observation_set_digest,
                "task outcome observation-set digest",
            ),
            (self.evaluation_digest, "task outcome evaluation digest"),
        ):
            digest(value, label)
        for value, label in (
            (self.source_bundle_id, "task outcome source bundle ID"),
            (self.output_bundle_id, "task outcome output bundle ID"),
            (self.verification_run_id, "task outcome verification run ID"),
            (self.evidence_finished_at, "task outcome evidence finish time"),
        ):
            string(value, label)
        validate_producer_authority(self.producer_authority)
        self._validate_snapshots()
        if type(self.verification_receipt_artifact) is not ArtifactRef:
            raise TypeError(
                "task outcome verification receipt must be an exact ArtifactRef"
            )
        if (
            self.verification_receipt_artifact.artifact_id
            != f"{self.verification_run_id}/receipt.json"
        ):
            raise ValueError("task outcome receipt artifact ID is inconsistent")
        if not isinstance(self.decision, TaskOutcomeDecision):
            raise ValueError("task outcome decision is invalid")
        if type(self.evaluation) is not ContractEvaluation:
            raise TypeError(
                "task outcome evaluation must be an exact ContractEvaluation"
            )
        if (
            self.evaluation.contract_digest != self.contract_digest
            or self.evaluation.observation_set_digest != self.observation_set_digest
            or self.evaluation.evaluation_digest != self.evaluation_digest
        ):
            raise ValueError("task outcome evaluation binding is inconsistent")
        if self.decision is not TaskOutcomeDecision.from_evaluation(self.evaluation):
            raise ValueError("task outcome decision is inconsistent with evaluation")
        if self.schema_version != "sisyphus_harness.task_outcome.v1":
            raise ValueError("unsupported task-outcome schema")

    def _validate_snapshots(self) -> None:
        if type(self.contract) is not EvidenceContract:
            raise TypeError("task outcome contract must be an exact EvidenceContract")
        if self.contract.contract_digest != self.contract_digest:
            raise ValueError("task outcome contract digest is inconsistent")
        if type(self.verification_profile) is not VerificationProfile:
            raise TypeError(
                "task outcome profile must be an exact VerificationProfile"
            )
        if self.verification_profile.profile_digest != self.verification_profile_digest:
            raise ValueError("task outcome profile digest is inconsistent")
        if (
            self.contract.verification_profile_digest
            != self.verification_profile.profile_digest
        ):
            raise ValueError("task outcome contract and profile are inconsistent")

    def content_payload(self) -> dict[str, object]:
        return WireModel.to_dict(self)

    @property
    def outcome_digest(self) -> str:
        return sha256_digest(self.content_payload())

    def to_dict(self) -> dict[str, object]:
        payload = self.content_payload()
        payload["outcome_digest"] = self.outcome_digest
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> TaskOutcome:
        fields = {
            "job_id",
            "attempt",
            "attempt_id",
            "attempt_digest",
            "source_bundle_id",
            "output_bundle_id",
            "contract_digest",
            "contract",
            "verification_run_id",
            "verification_request_digest",
            "verification_profile_digest",
            "verification_profile",
            "verification_receipt_digest",
            "verification_receipt_artifact",
            "producer_authority",
            "observation_set_digest",
            "evaluation_digest",
            "evaluation",
            "decision",
            "evidence_finished_at",
            "schema_version",
            "outcome_digest",
        }
        raw = strict_object(raw, required=fields, label="task outcome")
        result = cls(
            job_id=string(raw["job_id"], "task outcome job ID"),
            attempt=positive_integer(raw["attempt"], "task outcome attempt"),
            attempt_id=string(raw["attempt_id"], "task outcome attempt ID"),
            attempt_digest=digest(
                raw["attempt_digest"], "task outcome attempt digest"
            ),
            source_bundle_id=string(
                raw["source_bundle_id"], "task outcome source bundle ID"
            ),
            output_bundle_id=string(
                raw["output_bundle_id"], "task outcome output bundle ID"
            ),
            contract_digest=digest(
                raw["contract_digest"], "task outcome contract digest"
            ),
            contract=EvidenceContract.from_dict(raw["contract"]),
            verification_run_id=string(
                raw["verification_run_id"], "task outcome verification run ID"
            ),
            verification_request_digest=digest(
                raw["verification_request_digest"],
                "task outcome verification request digest",
            ),
            verification_profile_digest=digest(
                raw["verification_profile_digest"],
                "task outcome verification profile digest",
            ),
            verification_profile=VerificationProfile.from_dict(
                raw["verification_profile"]
            ),
            verification_receipt_digest=digest(
                raw["verification_receipt_digest"],
                "task outcome verification receipt digest",
            ),
            verification_receipt_artifact=ArtifactRef.from_dict(
                raw["verification_receipt_artifact"]
            ),
            producer_authority=validate_producer_authority(
                raw["producer_authority"]
            ),
            observation_set_digest=digest(
                raw["observation_set_digest"],
                "task outcome observation-set digest",
            ),
            evaluation_digest=digest(
                raw["evaluation_digest"], "task outcome evaluation digest"
            ),
            evaluation=ContractEvaluation.from_dict(raw["evaluation"]),
            decision=_decision(raw["decision"]),
            evidence_finished_at=string(
                raw["evidence_finished_at"],
                "task outcome evidence finish time",
            ),
            schema_version=string(raw["schema_version"], "task outcome schema"),
        )
        recorded = digest(raw["outcome_digest"], "task outcome digest")
        if recorded != result.outcome_digest:
            raise ValueError("task outcome digest does not match content")
        return result


def _decision(raw: object) -> TaskOutcomeDecision:
    value = string(raw, "task outcome decision")
    try:
        return TaskOutcomeDecision(value)
    except ValueError as exc:
        raise ValueError("task outcome decision is invalid") from exc
