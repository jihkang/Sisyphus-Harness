from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol, runtime_checkable

from ..contracts.control import AttemptFinished, TaskOutcome
from ..contracts.evidence_contract import EvidenceContract
from ..contracts.verification_service import VerificationProfile


_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


@dataclass(frozen=True, slots=True)
class TaskOutcomeRequest:
    job_id: str
    profile: VerificationProfile
    contract: EvidenceContract
    run_id: str
    producer_authority: str

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not self.job_id:
            raise ValueError("task outcome job ID must be non-empty")
        if type(self.profile) is not VerificationProfile:
            raise TypeError("task outcome profile must be a VerificationProfile")
        if type(self.contract) is not EvidenceContract:
            raise TypeError("task outcome contract must be an EvidenceContract")
        if self.contract.verification_profile_digest != self.profile.profile_digest:
            raise ValueError("task outcome profile does not match the EvidenceContract")
        if _SAFE_RUN_ID.fullmatch(self.run_id) is None or self.run_id in {".", ".."}:
            raise ValueError("task outcome run ID contains unsafe characters")
        if (
            not isinstance(self.producer_authority, str)
            or not self.producer_authority
            or len(self.producer_authority) > 256
            or self.producer_authority.strip() != self.producer_authority
            or any(character.isspace() for character in self.producer_authority)
            or any(ord(character) < 32 for character in self.producer_authority)
        ):
            raise ValueError("task outcome producer authority must be a bounded token")


@runtime_checkable
class TaskOutcomeAuthorityPort(Protocol):
    """Persistence boundary whose implementation is owned by Control."""

    def get_attempt_finished(self, job_id: str) -> AttemptFinished | None:
        ...

    def get_task_outcome(self, job_id: str) -> TaskOutcome | None:
        ...

    def publish_task_outcome(
        self,
        *,
        expected_attempt: AttemptFinished,
        outcome: TaskOutcome,
    ) -> TaskOutcome:
        ...


@runtime_checkable
class TaskOutcomeServicePort(Protocol):
    def adjudicate(self, request: TaskOutcomeRequest) -> TaskOutcome:
        ...
