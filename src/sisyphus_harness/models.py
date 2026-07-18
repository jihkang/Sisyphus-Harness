from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .contracts.verification import CommandResult, CommandSpec, VerificationReceipt


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: str
    idempotency_key: str
    kind: str
    payload: dict[str, Any]
    status: JobStatus
    lease_owner: str | None
    lease_expires_at: float | None
    attempts: int
    result: dict[str, Any] | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "idempotency_key": self.idempotency_key,
            "kind": self.kind,
            "payload": self.payload,
            "status": self.status.value,
            "lease_owner": self.lease_owner,
            "lease_expires_at": self.lease_expires_at,
            "attempts": self.attempts,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
