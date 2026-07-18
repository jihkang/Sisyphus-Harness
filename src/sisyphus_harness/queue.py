from __future__ import annotations

from datetime import UTC, datetime
import json
import math
from pathlib import Path
import time
from typing import Any
import uuid

from .database import Database
from .models import JobRecord, JobStatus


class LeaseError(RuntimeError):
    pass


class IdempotencyConflictError(RuntimeError):
    pass


class JobQueue:
    def __init__(self, database_path: Path) -> None:
        self.database = Database(database_path)
        self.database.initialize()

    def enqueue(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> JobRecord:
        normalized_kind = kind.strip()
        normalized_key = idempotency_key.strip()
        if not normalized_kind:
            raise ValueError("job kind must be non-empty")
        if not normalized_key:
            raise ValueError("idempotency key must be non-empty")
        now = _utc_now()
        job_id = f"job-{uuid.uuid4().hex}"
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO jobs(
                    job_id, idempotency_key, kind, payload_json, status,
                    created_at, updated_at
                )
                VALUES(?, ?, ?, ?, 'queued', ?, ?)
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                (job_id, normalized_key, normalized_kind, payload_json, now, now),
            )
            row = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?",
                (normalized_key,),
            ).fetchone()
        assert row is not None
        if row["kind"] != normalized_kind or row["payload_json"] != payload_json:
            raise IdempotencyConflictError(
                "idempotency key is already bound to a different request"
            )
        return _job_from_row(row)

    def claim(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        now: float | None = None,
    ) -> JobRecord | None:
        normalized_worker = worker_id.strip()
        if not normalized_worker:
            raise ValueError("worker ID must be non-empty")
        current, expires = _lease_window(lease_seconds, now)
        updated_at = _utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM jobs
                WHERE status = 'queued'
                   OR (status = 'running' AND lease_expires_at <= ?)
                ORDER BY created_at, job_id
                LIMIT 1
                """,
                (current,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE jobs
                SET status = 'running',
                    lease_owner = ?,
                    lease_expires_at = ?,
                    attempts = attempts + 1,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (normalized_worker, expires, updated_at, row["job_id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()
        assert claimed is not None
        return _job_from_row(claimed)

    def heartbeat(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_seconds: float,
        now: float | None = None,
    ) -> JobRecord:
        normalized_job_id = job_id.strip()
        normalized_worker = worker_id.strip()
        if not normalized_job_id:
            raise ValueError("job ID must be non-empty")
        if not normalized_worker:
            raise ValueError("worker ID must be non-empty")
        current, expires = _lease_window(lease_seconds, now)
        with self.database.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE jobs
                SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ?
                  AND status = 'running'
                  AND lease_owner = ?
                  AND lease_expires_at > ?
                """,
                (expires, _utc_now(), normalized_job_id, normalized_worker, current),
            )
            if updated.rowcount != 1:
                raise LeaseError("job lease is missing, expired, or owned by another worker")
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (normalized_job_id,),
            ).fetchone()
        assert row is not None
        return _job_from_row(row)

    def complete(
        self,
        job_id: str,
        *,
        worker_id: str,
        result: dict[str, Any],
        now: float | None = None,
    ) -> JobRecord:
        return self._finish(
            job_id,
            worker_id=worker_id,
            status=JobStatus.COMPLETED,
            result=result,
            now=now,
        )

    def fail(
        self,
        job_id: str,
        *,
        worker_id: str,
        result: dict[str, Any],
        now: float | None = None,
    ) -> JobRecord:
        return self._finish(
            job_id,
            worker_id=worker_id,
            status=JobStatus.FAILED,
            result=result,
            now=now,
        )

    def get(self, job_id: str) -> JobRecord | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def _finish(
        self,
        job_id: str,
        *,
        worker_id: str,
        status: JobStatus,
        result: dict[str, Any],
        now: float | None,
    ) -> JobRecord:
        normalized_job_id = job_id.strip()
        normalized_worker = worker_id.strip()
        if not normalized_job_id:
            raise ValueError("job ID must be non-empty")
        if not normalized_worker:
            raise ValueError("worker ID must be non-empty")
        current = time.time() if now is None else float(now)
        if not math.isfinite(current):
            raise ValueError("lease clock must be finite")
        result_json = json.dumps(result, separators=(",", ":"), sort_keys=True)
        with self.database.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE jobs
                SET status = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    result_json = ?,
                    updated_at = ?
                WHERE job_id = ?
                  AND status = 'running'
                  AND lease_owner = ?
                  AND lease_expires_at > ?
                """,
                (
                    status.value,
                    result_json,
                    _utc_now(),
                    normalized_job_id,
                    normalized_worker,
                    current,
                ),
            )
            if updated.rowcount != 1:
                raise LeaseError("job lease is missing, expired, or owned by another worker")
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (normalized_job_id,),
            ).fetchone()
        assert row is not None
        return _job_from_row(row)


def _job_from_row(row) -> JobRecord:
    return JobRecord(
        job_id=str(row["job_id"]),
        idempotency_key=str(row["idempotency_key"]),
        kind=str(row["kind"]),
        payload=json.loads(row["payload_json"]),
        status=JobStatus(str(row["status"])),
        lease_owner=str(row["lease_owner"]) if row["lease_owner"] is not None else None,
        lease_expires_at=(
            float(row["lease_expires_at"])
            if row["lease_expires_at"] is not None
            else None
        ),
        attempts=int(row["attempts"]),
        result=json.loads(row["result_json"]) if row["result_json"] is not None else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _lease_window(
    lease_seconds: float,
    now: float | None,
) -> tuple[float, float]:
    duration = float(lease_seconds)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("lease duration must be positive and finite")
    current = time.time() if now is None else float(now)
    if not math.isfinite(current):
        raise ValueError("lease clock must be finite")
    expires = current + duration
    if not math.isfinite(expires):
        raise ValueError("lease expiry must be finite")
    return current, expires
