from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sisyphus_harness.contracts.agent import AgentResult
from sisyphus_harness.contracts.control import (
    AttemptFinished,
    TaskOutcomeDecision,
)
from sisyphus_harness.infra.control_outcomes import (
    SQLiteTaskOutcomeAuthority,
    StaleAttemptError,
    TaskOutcomeConflictError,
)
from sisyphus_harness.evidence_contract import evaluate_evidence_contract
from sisyphus_harness.models import JobStatus
from sisyphus_harness.ports.control_outcomes import TaskOutcomeRequest
from sisyphus_harness.queue import JobQueue, LeaseError
from sisyphus_harness.services.control_outcomes import (
    ControlTaskOutcomeError,
    ControlTaskOutcomeService,
)
from sisyphus_harness.services.evidence_contract import ControlEvidenceContractService

from .test_evidence_adjudication import (
    ResultVerifier,
    _AUTHORITY,
    _bundle,
    _contract,
    _profile,
)


def _attempt(job_id: str, attempt: int, *, agent_success: bool) -> AttemptFinished:
    return AttemptFinished(
        job_id=job_id,
        attempt=attempt,
        attempt_id=f"{job_id}/attempt-{attempt:04d}",
        source_bundle=_bundle("a"),
        output_bundle=_bundle("b"),
        agent_result=AgentResult(
            run_id=f"agent-attempt-{attempt}",
            success=agent_success,
            reason="agent stopped",
            steps=1,
            compactions=0,
            verifications=0,
            workspace_state_before="before",
            workspace_state_after="after",
            changed_paths=("module.py",),
            artifact_path=f"agent/attempt-{attempt}",
        ),
    )


class ControlOutcomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "authority.sqlite3"
        self.queue = JobQueue(self.database_path)
        self.authority = SQLiteTaskOutcomeAuthority(self.database_path)

    def _claimed_job(self, *, now: float = 100):
        queued = self.queue.enqueue(
            kind="coding-agent",
            payload={"task": "fix"},
            idempotency_key=f"request-{now}",
        )
        claimed = self.queue.claim(
            worker_id="worker-1",
            lease_seconds=30,
            now=now,
        )
        assert claimed is not None
        self.assertEqual(claimed.job_id, queued.job_id)
        return claimed

    def _finished_attempt(self, *, agent_success: bool = False) -> AttemptFinished:
        claimed = self._claimed_job()
        attempt = _attempt(
            claimed.job_id,
            claimed.attempts,
            agent_success=agent_success,
        )
        completed = self.queue.finish_attempt(
            claimed.job_id,
            worker_id="worker-1",
            attempt=attempt,
            now=101,
        )
        self.assertEqual(completed.status, JobStatus.COMPLETED)
        return attempt

    def _request(self, job_id: str, *, run_id: str = "control-final-1"):
        profile = _profile()
        return TaskOutcomeRequest(
            job_id=job_id,
            profile=profile,
            contract=_contract(profile),
            run_id=run_id,
            producer_authority=_AUTHORITY,
        )

    def test_worker_attempt_is_execution_lineage_not_a_task_outcome(self) -> None:
        attempt = self._finished_attempt(agent_success=False)

        self.assertEqual(
            self.authority.get_attempt_finished(attempt.job_id),
            attempt,
        )
        self.assertIsNone(self.authority.get_task_outcome(attempt.job_id))
        self.assertFalse(attempt.agent_result.success)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.authority.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO task_outcomes(
                        job_id, attempt, attempt_digest, outcome_digest,
                        payload_json, published_at
                    )
                    VALUES(?, ?, ?, ?, '{}', 'now')
                    """,
                    (
                        attempt.job_id,
                        attempt.attempt,
                        "sha256:" + "f" * 64,
                        "sha256:" + "e" * 64,
                    ),
                )

    def test_expired_attempt_cannot_publish_after_reclaim(self) -> None:
        first = self._claimed_job()
        stale = _attempt(first.job_id, 1, agent_success=True)
        reclaimed = self.queue.claim(
            worker_id="worker-2",
            lease_seconds=30,
            now=131,
        )
        assert reclaimed is not None
        self.assertEqual(reclaimed.attempts, 2)

        with self.assertRaises(LeaseError):
            self.queue.finish_attempt(
                first.job_id,
                worker_id="worker-1",
                attempt=stale,
                now=132,
            )
        current = _attempt(first.job_id, 2, agent_success=False)
        self.queue.finish_attempt(
            first.job_id,
            worker_id="worker-2",
            attempt=current,
            now=132,
        )
        self.assertEqual(self.authority.get_attempt_finished(first.job_id), current)

    def test_low_level_queue_completion_cannot_fabricate_attempt_authority(self) -> None:
        claimed = self._claimed_job()
        self.queue.complete(
            claimed.job_id,
            worker_id="worker-1",
            result={"success": True},
            now=101,
        )

        self.assertIsNone(self.authority.get_attempt_finished(claimed.job_id))
        service = ControlTaskOutcomeService(
            ControlEvidenceContractService(ResultVerifier(command_passed=True)),
            self.authority,
        )
        with self.assertRaisesRegex(
            ControlTaskOutcomeError,
            "no authoritative AttemptFinished",
        ):
            service.adjudicate(self._request(claimed.job_id))

    def test_non_coding_job_cannot_publish_attempt_authority(self) -> None:
        queued = self.queue.enqueue(
            kind="maintenance",
            payload={"task": "not coding"},
            idempotency_key="maintenance-request",
        )
        claimed = self.queue.claim(
            worker_id="worker-1",
            lease_seconds=30,
            now=100,
        )
        assert claimed is not None

        with self.assertRaises(LeaseError):
            self.queue.finish_attempt(
                queued.job_id,
                worker_id="worker-1",
                attempt=_attempt(queued.job_id, 1, agent_success=True),
                now=101,
            )
        self.assertIsNone(self.authority.get_attempt_finished(queued.job_id))

    def test_control_rechecks_job_kind_before_outcome_publication(self) -> None:
        attempt = self._finished_attempt(agent_success=True)
        with self.authority.database.transaction() as connection:
            connection.execute(
                "UPDATE jobs SET kind = 'maintenance' WHERE job_id = ?",
                (attempt.job_id,),
            )

        service = ControlTaskOutcomeService(
            ControlEvidenceContractService(ResultVerifier(command_passed=True)),
            self.authority,
        )
        with self.assertRaisesRegex(StaleAttemptError, "coding-agent job"):
            service.adjudicate(self._request(attempt.job_id))
        self.assertIsNone(self.authority.get_task_outcome(attempt.job_id))

    def test_only_control_publishes_evidence_bound_semantic_outcome(self) -> None:
        attempt = self._finished_attempt(agent_success=False)
        verifier = ResultVerifier(command_passed=True)
        service = ControlTaskOutcomeService(
            ControlEvidenceContractService(verifier),
            self.authority,
        )

        request = self._request(attempt.job_id)
        outcome = service.adjudicate(request)

        self.assertEqual(outcome.decision, TaskOutcomeDecision.PASSED)
        self.assertEqual(outcome.evaluation.logical_result.value, "pass")
        self.assertEqual(outcome.producer_authority, _AUTHORITY)
        self.assertEqual(outcome.contract, request.contract)
        self.assertEqual(outcome.verification_profile, request.profile)
        self.assertEqual(outcome.attempt_digest, attempt.attempt_digest)
        self.assertEqual(outcome.output_bundle_id, attempt.output_bundle.bundle_id)
        self.assertEqual(
            self.authority.get_task_outcome(attempt.job_id),
            outcome,
        )
        self.assertFalse(attempt.agent_result.success)

    def test_failed_and_missing_evidence_map_without_agent_override(self) -> None:
        attempt = self._finished_attempt(agent_success=True)
        request = self._request(attempt.job_id)
        outcome = ControlTaskOutcomeService(
            ControlEvidenceContractService(ResultVerifier(command_passed=False)),
            self.authority,
        ).adjudicate(request)

        self.assertEqual(outcome.decision, TaskOutcomeDecision.FAILED)
        missing = evaluate_evidence_contract(request.contract, ())
        self.assertEqual(
            TaskOutcomeDecision.from_evaluation(missing),
            TaskOutcomeDecision.INDETERMINATE,
        )

    def test_control_publish_is_idempotent_and_conflicting_inputs_fail(self) -> None:
        attempt = self._finished_attempt(agent_success=True)
        verifier = ResultVerifier(command_passed=True)
        service = ControlTaskOutcomeService(
            ControlEvidenceContractService(verifier),
            self.authority,
        )
        request = self._request(attempt.job_id)
        first = service.adjudicate(request)

        self.assertEqual(service.adjudicate(request), first)
        self.assertEqual(len(verifier.requests), 1)
        with self.assertRaisesRegex(
            ControlTaskOutcomeError,
            "different Control inputs",
        ):
            service.adjudicate(
                self._request(attempt.job_id, run_id="control-final-2")
            )
        with self.assertRaisesRegex(
            ControlTaskOutcomeError,
            "different Control inputs",
        ):
            service.adjudicate(
                replace(request, producer_authority="control.other")
            )
        with self.assertRaises(TaskOutcomeConflictError):
            self.authority.publish_task_outcome(
                expected_attempt=attempt,
                outcome=replace(first, producer_authority="control.other"),
            )
        with self.authority.database.transaction() as connection:
            connection.execute(
                "UPDATE jobs SET result_json = '{}' WHERE job_id = ?",
                (attempt.job_id,),
            )
        with self.assertRaises(StaleAttemptError):
            service.adjudicate(request)

    def test_outcome_publish_rejects_stale_binding_and_rows_are_immutable(self) -> None:
        attempt = self._finished_attempt(agent_success=True)
        service = ControlTaskOutcomeService(
            ControlEvidenceContractService(ResultVerifier(command_passed=True)),
            self.authority,
        )
        outcome = service.adjudicate(self._request(attempt.job_id))
        stale = replace(attempt, output_bundle=_bundle("c"))

        with self.assertRaises(StaleAttemptError):
            self.authority.publish_task_outcome(
                expected_attempt=stale,
                outcome=outcome,
            )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.authority.database.transaction() as connection:
                connection.execute(
                    "UPDATE task_outcomes SET payload_json = '{}' WHERE job_id = ?",
                    (attempt.job_id,),
                )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.authority.database.transaction() as connection:
                connection.execute(
                    "DELETE FROM attempt_finished WHERE job_id = ?",
                    (attempt.job_id,),
                )

    def test_control_rejects_an_adjudicator_rebound_to_another_bundle(self) -> None:
        attempt = self._finished_attempt(agent_success=True)
        canonical = ControlEvidenceContractService(
            ResultVerifier(command_passed=True)
        )

        class ReboundAdjudicator:
            def adjudicate(self, request):
                result = canonical.adjudicate(request)
                rebound = replace(
                    result.verification_request,
                    workspace_bundle=_bundle("c"),
                )
                object.__setattr__(result, "verification_request", rebound)
                return result

        service = ControlTaskOutcomeService(
            ReboundAdjudicator(),
            self.authority,
        )

        with self.assertRaisesRegex(
            ControlTaskOutcomeError,
            "not bound to the authoritative attempt",
        ):
            service.adjudicate(self._request(attempt.job_id))
        self.assertIsNone(self.authority.get_task_outcome(attempt.job_id))


if __name__ == "__main__":
    unittest.main()
