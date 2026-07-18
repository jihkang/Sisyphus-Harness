from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from sisyphus_harness.models import JobStatus
from sisyphus_harness.queue import (
    IdempotencyConflictError,
    JobQueue,
    LeaseError,
)


class QueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        path = Path(self.temporary_directory.name) / "authority.sqlite3"
        self.queue = JobQueue(path)

    def test_enqueue_is_idempotent_for_the_same_request(self) -> None:
        first = self.queue.enqueue(
            kind="agent-run",
            payload={"task": "fix"},
            idempotency_key="request-1",
        )
        second = self.queue.enqueue(
            kind="agent-run",
            payload={"task": "fix"},
            idempotency_key="request-1",
        )

        self.assertEqual(first.job_id, second.job_id)
        self.assertEqual(first.status, JobStatus.QUEUED)

    def test_idempotency_key_reuse_with_different_request_is_rejected(self) -> None:
        self.queue.enqueue(
            kind="agent-run",
            payload={"task": "first"},
            idempotency_key="request-1",
        )

        with self.assertRaises(IdempotencyConflictError):
            self.queue.enqueue(
                kind="agent-run",
                payload={"task": "second"},
                idempotency_key="request-1",
            )

    def test_only_one_concurrent_worker_claims_a_single_job(self) -> None:
        queued = self.queue.enqueue(
            kind="agent-run",
            payload={"task": "fix"},
            idempotency_key="request-1",
        )

        def claim(index: int):
            return self.queue.claim(
                worker_id=f"worker-{index}",
                lease_seconds=60,
                now=100,
            )

        with ThreadPoolExecutor(max_workers=16) as executor:
            claims = list(executor.map(claim, range(32)))

        claimed = [job for job in claims if job is not None]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].job_id, queued.job_id)
        self.assertEqual(claimed[0].attempts, 1)

    def test_expired_lease_is_reclaimed_and_old_owner_loses_authority(self) -> None:
        queued = self.queue.enqueue(
            kind="agent-run",
            payload={"task": "fix"},
            idempotency_key="request-1",
        )
        first = self.queue.claim(worker_id="worker-1", lease_seconds=5, now=100)
        self.assertIsNotNone(first)

        second = self.queue.claim(worker_id="worker-2", lease_seconds=10, now=106)

        assert second is not None
        self.assertEqual(second.job_id, queued.job_id)
        self.assertEqual(second.lease_owner, "worker-2")
        self.assertEqual(second.attempts, 2)
        with self.assertRaises(LeaseError):
            self.queue.complete(
                queued.job_id,
                worker_id="worker-1",
                result={"ok": True},
                now=107,
            )
        completed = self.queue.complete(
            queued.job_id,
            worker_id="worker-2",
            result={"ok": True},
            now=107,
        )
        self.assertEqual(completed.status, JobStatus.COMPLETED)
        self.assertIsNone(completed.lease_owner)
        self.assertEqual(completed.result, {"ok": True})

    def test_heartbeat_requires_current_unexpired_owner(self) -> None:
        queued = self.queue.enqueue(
            kind="agent-run",
            payload={"task": "fix"},
            idempotency_key="request-1",
        )
        self.queue.claim(worker_id="worker-1", lease_seconds=5, now=100)

        renewed = self.queue.heartbeat(
            queued.job_id,
            worker_id="worker-1",
            lease_seconds=10,
            now=104,
        )
        self.assertEqual(renewed.lease_expires_at, 114)
        with self.assertRaises(LeaseError):
            self.queue.heartbeat(
                queued.job_id,
                worker_id="worker-2",
                lease_seconds=10,
                now=105,
            )
        with self.assertRaises(LeaseError):
            self.queue.heartbeat(
                queued.job_id,
                worker_id="worker-1",
                lease_seconds=10,
                now=115,
            )

    def test_failed_job_is_terminal_and_not_claimable(self) -> None:
        queued = self.queue.enqueue(
            kind="agent-run",
            payload={"task": "fix"},
            idempotency_key="request-1",
        )
        self.queue.claim(worker_id="worker-1", lease_seconds=5, now=100)
        failed = self.queue.fail(
            queued.job_id,
            worker_id="worker-1",
            result={"error": "failed"},
            now=101,
        )

        self.assertEqual(failed.status, JobStatus.FAILED)
        self.assertIsNone(
            self.queue.claim(worker_id="worker-2", lease_seconds=5, now=102)
        )

    def test_lease_duration_and_clock_must_be_finite(self) -> None:
        self.queue.enqueue(
            kind="agent-run",
            payload={"task": "fix"},
            idempotency_key="request-1",
        )
        for duration in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(duration=duration):
                with self.assertRaisesRegex(ValueError, "positive and finite"):
                    self.queue.claim(
                        worker_id="worker-1",
                        lease_seconds=duration,
                        now=100,
                    )
        with self.assertRaisesRegex(ValueError, "clock must be finite"):
            self.queue.claim(
                worker_id="worker-1",
                lease_seconds=5,
                now=float("nan"),
            )


if __name__ == "__main__":
    unittest.main()
