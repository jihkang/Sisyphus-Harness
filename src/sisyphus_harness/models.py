from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .contracts.codec import WireModel
from .contracts.verification import CommandResult, CommandSpec, VerificationReceipt


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class JobRecord(WireModel):
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
