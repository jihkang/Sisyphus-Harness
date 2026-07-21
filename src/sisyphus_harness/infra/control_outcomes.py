from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ..contracts.codec import canonical_json_bytes, loads_strict_json
from ..contracts.control import AttemptFinished, TaskOutcome
from ..database import Database


class TaskOutcomeAuthorityError(RuntimeError):
    pass


class StaleAttemptError(TaskOutcomeAuthorityError):
    pass


class TaskOutcomeConflictError(TaskOutcomeAuthorityError):
    pass


class SQLiteTaskOutcomeAuthority:
    """Control-owned fenced store for attempt lineage and semantic outcomes."""

    def __init__(self, database_path: Path) -> None:
        self.database = Database(database_path)
        self.database.initialize()

    def get_attempt_finished(self, job_id: str) -> AttemptFinished | None:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT job_id, attempt, attempt_id, attempt_digest, payload_json
                FROM attempt_finished
                WHERE job_id = ?
                ORDER BY attempt DESC
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        return _attempt_from_row(row) if row is not None else None

    def get_task_outcome(self, job_id: str) -> TaskOutcome | None:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT job_id, attempt, attempt_digest, outcome_digest, payload_json
                FROM task_outcomes
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        return _outcome_from_row(row) if row is not None else None

    def publish_task_outcome(
        self,
        *,
        expected_attempt: AttemptFinished,
        outcome: TaskOutcome,
    ) -> TaskOutcome:
        if type(expected_attempt) is not AttemptFinished:
            raise TypeError("expected attempt must be an exact AttemptFinished")
        if type(outcome) is not TaskOutcome:
            raise TypeError("outcome must be an exact TaskOutcome")
        _validate_outcome_binding(expected_attempt, outcome)
        payload_json = canonical_json_bytes(outcome.to_dict()).decode("ascii")
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT
                    jobs.kind,
                    jobs.status,
                    jobs.attempts,
                    jobs.result_json,
                    attempt_finished.attempt_digest,
                    attempt_finished.payload_json
                FROM jobs
                JOIN attempt_finished
                  ON attempt_finished.job_id = jobs.job_id
                 AND attempt_finished.attempt = jobs.attempts
                WHERE jobs.job_id = ?
                """,
                (expected_attempt.job_id,),
            ).fetchone()
            if row is None:
                raise StaleAttemptError(
                    "job has no authoritative finished attempt at its current fence"
                )
            if row["kind"] != "coding-agent":
                raise StaleAttemptError(
                    "finished attempt is not bound to a coding-agent job"
                )
            if row["status"] != "completed":
                raise StaleAttemptError("job execution is not completed")
            if int(row["attempts"]) != expected_attempt.attempt:
                raise StaleAttemptError("attempt number no longer owns job authority")
            stored_attempt = AttemptFinished.from_dict(
                loads_strict_json(
                    str(row["payload_json"]),
                    label="stored attempt-finished result",
                )
            )
            if (
                stored_attempt != expected_attempt
                or str(row["attempt_digest"]) != expected_attempt.attempt_digest
                or row["result_json"] != row["payload_json"]
            ):
                raise StaleAttemptError(
                    "attempt payload does not match authoritative queue state"
                )
            existing = connection.execute(
                """
                SELECT job_id, attempt, attempt_digest, outcome_digest, payload_json
                FROM task_outcomes
                WHERE job_id = ?
                """,
                (expected_attempt.job_id,),
            ).fetchone()
            if existing is not None:
                published = _outcome_from_row(existing)
                if published != outcome:
                    raise TaskOutcomeConflictError(
                        "job already has a different immutable task outcome"
                    )
                return published
            connection.execute(
                """
                INSERT INTO task_outcomes(
                    job_id, attempt, attempt_digest, outcome_digest,
                    payload_json, published_at
                )
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome.job_id,
                    outcome.attempt,
                    outcome.attempt_digest,
                    outcome.outcome_digest,
                    payload_json,
                    _utc_now(),
                ),
            )
        return outcome


def _validate_outcome_binding(
    attempt: AttemptFinished,
    outcome: TaskOutcome,
) -> None:
    if (
        outcome.job_id != attempt.job_id
        or outcome.attempt != attempt.attempt
        or outcome.attempt_id != attempt.attempt_id
        or outcome.attempt_digest != attempt.attempt_digest
        or outcome.source_bundle_id != attempt.source_bundle.bundle_id
        or outcome.output_bundle_id != attempt.output_bundle.bundle_id
    ):
        raise StaleAttemptError("task outcome is not bound to the expected attempt")


def _attempt_from_row(row) -> AttemptFinished:
    attempt = AttemptFinished.from_dict(
        loads_strict_json(
            str(row["payload_json"]),
            label="stored attempt-finished result",
        )
    )
    if (
        str(row["job_id"]) != attempt.job_id
        or int(row["attempt"]) != attempt.attempt
        or str(row["attempt_id"]) != attempt.attempt_id
        or str(row["attempt_digest"]) != attempt.attempt_digest
    ):
        raise TaskOutcomeAuthorityError(
            "attempt-finished columns do not match the stored payload"
        )
    return attempt


def _outcome_from_row(row) -> TaskOutcome:
    outcome = TaskOutcome.from_dict(
        loads_strict_json(
            str(row["payload_json"]),
            label="stored task outcome",
        )
    )
    if (
        str(row["job_id"]) != outcome.job_id
        or int(row["attempt"]) != outcome.attempt
        or str(row["attempt_digest"]) != outcome.attempt_digest
        or str(row["outcome_digest"]) != outcome.outcome_digest
    ):
        raise TaskOutcomeAuthorityError(
            "task-outcome columns do not match the stored payload"
        )
    return outcome


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
