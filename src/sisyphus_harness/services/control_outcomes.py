from __future__ import annotations

from dataclasses import dataclass

from ..contracts.control import TaskOutcome, TaskOutcomeDecision
from ..ports.control_outcomes import (
    TaskOutcomeAuthorityPort,
    TaskOutcomeRequest,
)
from ..ports.evidence_contracts import (
    EvidenceAdjudicationRequest,
    EvidenceAdjudicationResult,
    EvidenceContractAdjudicationPort,
)


class ControlTaskOutcomeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ControlTaskOutcomeService:
    """Load current attempt lineage, adjudicate it, and publish exactly once."""

    adjudicator: EvidenceContractAdjudicationPort
    authority: TaskOutcomeAuthorityPort

    def __post_init__(self) -> None:
        if not isinstance(self.adjudicator, EvidenceContractAdjudicationPort):
            raise TypeError(
                "adjudicator must implement EvidenceContractAdjudicationPort"
            )
        if not isinstance(self.authority, TaskOutcomeAuthorityPort):
            raise TypeError("authority must implement TaskOutcomeAuthorityPort")

    def adjudicate(self, request: TaskOutcomeRequest) -> TaskOutcome:
        if type(request) is not TaskOutcomeRequest:
            raise TypeError("task outcome request must be an exact TaskOutcomeRequest")
        attempt = self.authority.get_attempt_finished(request.job_id)
        if attempt is None:
            raise ControlTaskOutcomeError(
                "job has no authoritative AttemptFinished record"
            )
        existing = self.authority.get_task_outcome(request.job_id)
        if existing is not None:
            if (
                existing.contract_digest != request.contract.contract_digest
                or existing.verification_profile_digest
                != request.profile.profile_digest
                or existing.verification_run_id != request.run_id
                or existing.producer_authority != request.producer_authority
            ):
                raise ControlTaskOutcomeError(
                    "job already has an outcome bound to different Control inputs"
                )
            return self.authority.publish_task_outcome(
                expected_attempt=attempt,
                outcome=existing,
            )
        adjudication = self.adjudicator.adjudicate(
            EvidenceAdjudicationRequest(
                job_result=attempt,
                profile=request.profile,
                contract=request.contract,
                run_id=request.run_id,
                producer_authority=request.producer_authority,
            )
        )
        if type(adjudication) is not EvidenceAdjudicationResult:
            raise ControlTaskOutcomeError(
                "adjudicator returned an invalid evidence result"
            )
        verification_request = adjudication.verification_request
        verification = adjudication.verification_result
        if (
            adjudication.job_id != attempt.job_id
            or adjudication.attempt_id != attempt.attempt_id
            or adjudication.output_bundle_id != attempt.output_bundle.bundle_id
            or verification_request.run_id != request.run_id
            or verification_request.workspace_bundle != attempt.output_bundle
            or verification_request.profile != request.profile
            or verification.request_digest != verification_request.request_digest
            or verification.workspace_bundle_id != attempt.output_bundle.bundle_id
            or verification.profile_digest != request.profile.profile_digest
            or verification.receipt.run_id != request.run_id
        ):
            raise ControlTaskOutcomeError(
                "adjudication result is not bound to the authoritative attempt"
            )
        evaluation = adjudication.evaluation
        outcome = TaskOutcome(
            job_id=attempt.job_id,
            attempt=attempt.attempt,
            attempt_id=attempt.attempt_id,
            attempt_digest=attempt.attempt_digest,
            source_bundle_id=attempt.source_bundle.bundle_id,
            output_bundle_id=attempt.output_bundle.bundle_id,
            contract_digest=request.contract.contract_digest,
            contract=request.contract,
            verification_run_id=adjudication.verification_request.run_id,
            verification_request_digest=verification.request_digest,
            verification_profile_digest=verification.profile_digest,
            verification_profile=request.profile,
            verification_receipt_digest=verification.receipt.receipt_digest,
            verification_receipt_artifact=verification.receipt_artifact,
            producer_authority=request.producer_authority,
            observation_set_digest=evaluation.observation_set_digest,
            evaluation_digest=evaluation.evaluation_digest,
            evaluation=evaluation,
            decision=TaskOutcomeDecision.from_evaluation(evaluation),
            evidence_finished_at=verification.receipt.finished_at,
        )
        return self.authority.publish_task_outcome(
            expected_attempt=attempt,
            outcome=outcome,
        )
