from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol, runtime_checkable

from ..contracts.control import AttemptFinished
from ..contracts.evidence_contract import (
    ContractEvaluation,
    EvidenceContract,
    EvidenceObservation,
)
from ..contracts.verification_service import (
    BundleVerificationRequest,
    VerificationProfile,
    VerificationServiceResult,
)


_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


@dataclass(frozen=True, slots=True)
class EvidenceAdjudicationRequest:
    """Control-owned inputs for an independent final verification run.

    The attempt result is execution lineage, not a completion assertion.  In
    particular, ``agent_result.success`` is never consumed as contract evidence.
    """

    job_result: AttemptFinished
    profile: VerificationProfile
    contract: EvidenceContract
    run_id: str
    producer_authority: str

    def __post_init__(self) -> None:
        if type(self.job_result) is not AttemptFinished:
            raise TypeError("adjudication job result must be an AttemptFinished")
        if type(self.profile) is not VerificationProfile:
            raise TypeError("adjudication profile must be a VerificationProfile")
        if type(self.contract) is not EvidenceContract:
            raise TypeError("adjudication contract must be an EvidenceContract")
        if self.contract.verification_profile_digest != self.profile.profile_digest:
            raise ValueError(
                "adjudication profile does not match the EvidenceContract"
            )
        if _SAFE_RUN_ID.fullmatch(self.run_id) is None or self.run_id in {".", ".."}:
            raise ValueError("adjudication run ID contains unsafe characters")
        if (
            not isinstance(self.producer_authority, str)
            or not self.producer_authority
            or len(self.producer_authority) > 256
            or self.producer_authority.strip() != self.producer_authority
            or any(character.isspace() for character in self.producer_authority)
            or any(ord(character) < 32 for character in self.producer_authority)
        ):
            raise ValueError(
                "adjudication producer authority must be a bounded token"
            )


@dataclass(frozen=True, slots=True)
class EvidenceAdjudicationResult:
    """Immutable shadow result; it has no task or queue mutation operation."""

    job_id: str
    attempt_id: str
    output_bundle_id: str
    agent_reported_success: bool
    verification_request: BundleVerificationRequest
    verification_result: VerificationServiceResult
    observations: tuple[EvidenceObservation, ...]
    evaluation: ContractEvaluation

    def __post_init__(self) -> None:
        if not self.job_id or not self.attempt_id or not self.output_bundle_id:
            raise ValueError("adjudication result identity must be non-empty")
        if not isinstance(self.agent_reported_success, bool):
            raise TypeError("agent reported success must be a boolean")
        if not isinstance(self.verification_request, BundleVerificationRequest):
            raise TypeError("adjudication verification request is invalid")
        if not isinstance(self.verification_result, VerificationServiceResult):
            raise TypeError("adjudication verification result is invalid")
        if type(self.observations) is not tuple:
            raise TypeError("adjudication observations must be a built-in tuple")
        if any(type(item) is not EvidenceObservation for item in self.observations):
            raise TypeError(
                "adjudication observations must be exact EvidenceObservation values"
            )
        if not isinstance(self.evaluation, ContractEvaluation):
            raise TypeError("adjudication contract evaluation is invalid")
        if self.output_bundle_id != self.verification_request.workspace_bundle.bundle_id:
            raise ValueError(
                "adjudication output bundle does not match the verification request"
            )
        if self.verification_result.request_digest != self.verification_request.request_digest:
            raise ValueError(
                "adjudication verification result does not match its request"
            )
        if self.verification_result.workspace_bundle_id != self.output_bundle_id:
            raise ValueError(
                "adjudication verification result does not match the output bundle"
            )
        if (
            self.verification_result.profile_digest
            != self.verification_request.profile.profile_digest
        ):
            raise ValueError(
                "adjudication verification result does not match the profile"
            )
        if self.verification_result.receipt.run_id != self.verification_request.run_id:
            raise ValueError("adjudication receipt does not match the verification run")


@runtime_checkable
class ReceiptObservationPort(Protocol):
    """Normalize verifier facts without interpreting acceptance semantics."""

    @property
    def adapter_digest(self) -> str:
        ...

    def adapt(
        self,
        *,
        request: BundleVerificationRequest,
        result: VerificationServiceResult,
        producer_authority: str,
    ) -> tuple[EvidenceObservation, ...]:
        ...


@runtime_checkable
class EvidenceContractAdjudicationPort(Protocol):
    """Control-side task-contract adjudication boundary."""

    def adjudicate(
        self,
        request: EvidenceAdjudicationRequest,
    ) -> EvidenceAdjudicationResult:
        ...
