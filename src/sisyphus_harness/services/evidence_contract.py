from __future__ import annotations

from dataclasses import dataclass, field

from ..adapters.receipt_observations import (
    ReceiptObservationAdapter,
    VerificationBindingError,
    validate_final_command_observations,
    validate_final_verification_bindings,
)
from ..contracts.verification import VerificationReceipt
from ..contracts.verification_service import (
    BundleVerificationRequest,
    VerificationServiceResult,
)
from ..evidence_contract import evaluate_evidence_contract
from ..ports.evidence_contracts import (
    EvidenceAdjudicationRequest,
    EvidenceAdjudicationResult,
    ReceiptObservationPort,
)
from ..ports.verification_service import VerificationServicePort


@dataclass(frozen=True, slots=True)
class ControlEvidenceContractService:
    """Run exact-bundle verification and evaluate its facts in Control.

    This pure boundary returns immutable evidence and an evaluation.  Publishing
    the semantic result remains a separate Control-owned transaction.
    """

    verifier: VerificationServicePort
    observation_adapter: ReceiptObservationPort = field(
        default_factory=ReceiptObservationAdapter
    )

    def __post_init__(self) -> None:
        if not isinstance(self.verifier, VerificationServicePort):
            raise TypeError("verifier must implement VerificationServicePort")
        if not isinstance(self.observation_adapter, ReceiptObservationPort):
            raise TypeError(
                "observation adapter must implement ReceiptObservationPort"
            )

    def adjudicate(
        self,
        request: EvidenceAdjudicationRequest,
    ) -> EvidenceAdjudicationResult:
        if type(request) is not EvidenceAdjudicationRequest:
            raise TypeError(
                "adjudication request must be an exact EvidenceAdjudicationRequest"
            )
        if (
            self.observation_adapter.adapter_digest
            != request.contract.observation_adapter_digest
        ):
            raise VerificationBindingError(
                "observation adapter does not match the EvidenceContract"
            )
        verification_request = BundleVerificationRequest(
            run_id=request.run_id,
            # The only candidate input is the immutable bundle published by the
            # completed coding attempt.  Agent booleans are not consulted.
            workspace_bundle=request.job_result.output_bundle,
            profile=request.profile,
        )
        verification_result = self.verifier.execute(verification_request)
        # Control validates the boundary even when a custom normalization adapter
        # is injected, so adapters cannot accidentally bypass exact-run fencing.
        validate_final_verification_bindings(
            verification_request,
            verification_result,
        )
        authoritative_receipt = self.verifier.read_receipt(
            verification_result.receipt_artifact
        )
        if type(authoritative_receipt) is not VerificationReceipt:
            raise VerificationBindingError(
                "authoritative receipt reader returned an invalid receipt"
            )
        if authoritative_receipt != verification_result.receipt:
            raise VerificationBindingError(
                "inline verification receipt does not match its authoritative artifact"
            )
        # Use the freshly parsed authoritative value for every subsequent binding
        # check and observation.  This prevents a transport-owned object from
        # becoming the evidence source merely because it compared equal once.
        verification_result = VerificationServiceResult(
            request_digest=verification_result.request_digest,
            workspace_bundle_id=verification_result.workspace_bundle_id,
            profile_digest=verification_result.profile_digest,
            receipt=authoritative_receipt,
            receipt_artifact=verification_result.receipt_artifact,
            schema_version=verification_result.schema_version,
        )
        validate_final_verification_bindings(
            verification_request,
            verification_result,
        )
        observations = tuple(
            item
            for item in self.observation_adapter.adapt(
                request=verification_request,
                result=verification_result,
                producer_authority=request.producer_authority,
            )
        )
        validate_final_command_observations(
            verification_request,
            verification_result,
            observations,
            producer_authority=request.producer_authority,
        )
        evaluation = evaluate_evidence_contract(request.contract, observations)
        return EvidenceAdjudicationResult(
            job_id=request.job_result.job_id,
            attempt_id=request.job_result.attempt_id,
            output_bundle_id=request.job_result.output_bundle.bundle_id,
            agent_reported_success=request.job_result.agent_result.success,
            verification_request=verification_request,
            verification_result=verification_result,
            observations=observations,
            evaluation=evaluation,
        )
