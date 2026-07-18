from __future__ import annotations

from collections import deque
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import textwrap
import time
import unittest

from sisyphus_harness.authority import authority_database_path
from sisyphus_harness.models import JobStatus
from sisyphus_harness.provider import ChatResponse, ProviderError
from sisyphus_harness.queue import JobQueue
from sisyphus_harness.worker import (
    CodingJobPayload,
    CodingWorker,
    LeaseKeeper,
    WorkerError,
)

from .helpers import create_git_repo, run_git


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = deque(responses)

    def complete(self, messages) -> ChatResponse:
        return ChatResponse(content=self.responses.popleft())


class FailingProvider:
    def complete(self, messages) -> ChatResponse:
        raise ProviderError("offline")


def action(tool: str, arguments: dict[str, object]) -> str:
    return json.dumps(
        {
            "type": "tool",
            "tool": tool,
            "arguments": arguments,
            "reason": "worker test",
        }
    )


class FakeHeartbeatQueue:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def heartbeat(self, *args, **kwargs) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("lease lost")


class WorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repository = create_git_repo(
            Path(self.temporary_directory.name) / "repository"
        )

    def write_fixture(self) -> str:
        original = "def add(left, right):\n    return left - right\n"
        (self.repository / "calc.py").write_text(original, encoding="utf-8")
        (self.repository / "sisyphus-harness.toml").write_text(
            textwrap.dedent(
                f"""
                [provider]
                base_url = "http://127.0.0.1:8080/v1"
                model = "local"

                [agent]
                max_steps = 6
                max_runtime_seconds = 60
                max_file_bytes = 4096
                max_tool_output_chars = 4000
                max_protocol_errors = 1
                max_compactions = 2

                [cadence]
                compaction_interval_steps = 4
                context_char_limit = 4000
                keep_recent_events = 2
                reflection_interval_steps = 2
                observation_interval_steps = 2
                verification_interval_mutations = 2
                stagnation_limit = 4

                [commands.behavior]
                argv = {json.dumps([sys.executable, "-c", "from calc import add; assert add(2, 3) == 5"])}
                timeout_seconds = 10
                criteria = ["addition passes"]

                [verify]
                commands = ["behavior"]
                """
            ),
            encoding="utf-8",
        )
        run_git(self.repository, "add", "--all")
        run_git(self.repository, "commit", "-q", "-m", "add worker fixture")
        return original

    def enqueue(
        self,
        payload: dict[str, object],
        *,
        kind: str = "coding-agent",
        key: str = "request-1",
    ):
        return JobQueue(authority_database_path(self.repository)).enqueue(
            kind=kind,
            payload=payload,
            idempotency_key=key,
        )

    def test_worker_claims_runs_and_completes_coding_job(self) -> None:
        original = self.write_fixture()
        expected_hash = (
            "sha256:" + hashlib.sha256(original.encode("utf-8")).hexdigest()
        )
        self.enqueue(
            {
                "task": "Fix add.",
                "criteria": ["addition passes"],
                "config": "sisyphus-harness.toml",
                "policy": "config",
                "run_id": "worker-agent",
            }
        )
        provider = FakeProvider(
            [
                action("read_file", {"path": "calc.py"}),
                action(
                    "replace_text",
                    {
                        "path": "calc.py",
                        "old": "return left - right",
                        "new": "return left + right",
                        "expected_sha256": expected_hash,
                    },
                ),
                json.dumps({"type": "finish", "summary": "fixed"}),
            ]
        )
        worker = CodingWorker(
            self.repository,
            provider_factory=lambda settings: provider,
        )

        completed = worker.run_once(worker_id="worker-1", lease_seconds=30)

        assert completed is not None
        self.assertEqual(completed.status, JobStatus.COMPLETED)
        self.assertTrue(completed.result["success"])
        self.assertEqual(completed.attempts, 1)
        self.assertIsNone(
            worker.run_once(worker_id="worker-1", lease_seconds=30)
        )

    def test_worker_fails_unsupported_kind_and_invalid_payload(self) -> None:
        self.enqueue(
            {"task": "noop"},
            kind="unknown",
            key="unknown-kind",
        )
        worker = CodingWorker(self.repository)
        failed_kind = worker.run_once(worker_id="worker", lease_seconds=30)
        assert failed_kind is not None
        self.assertEqual(failed_kind.status, JobStatus.FAILED)
        self.assertIn("unsupported job kind", failed_kind.result["reason"])

        self.enqueue(
            {"task": "fix", "criteria": []},
            key="invalid-payload",
        )
        failed_payload = worker.run_once(worker_id="worker", lease_seconds=30)
        assert failed_payload is not None
        self.assertEqual(failed_payload.status, JobStatus.FAILED)
        self.assertIn("criteria", failed_payload.result["reason"])

    def test_provider_failure_is_recorded_as_failed_job(self) -> None:
        self.write_fixture()
        self.enqueue(
            {
                "task": "Fix add.",
                "criteria": ["addition passes"],
                "config": "sisyphus-harness.toml",
                "policy": "config",
            }
        )
        worker = CodingWorker(
            self.repository,
            provider_factory=lambda settings: FailingProvider(),
        )

        failed = worker.run_once(worker_id="worker", lease_seconds=30)

        assert failed is not None
        self.assertEqual(failed.status, JobStatus.FAILED)
        self.assertIn("provider failure", failed.result["reason"])

    def test_payload_parser_is_strict(self) -> None:
        payload = CodingJobPayload.from_dict(
            {
                "task": " Fix ",
                "criteria": [" one "],
                "config": "config.toml",
                "policy": "active",
                "run_id": "run",
            }
        )
        self.assertEqual(payload.task, "Fix")
        self.assertEqual(payload.criteria, ("one",))
        with self.assertRaisesRegex(WorkerError, "unknown fields"):
            CodingJobPayload.from_dict(
                {"task": "fix", "criteria": ["one"], "shell": "pwd"}
            )
        with self.assertRaisesRegex(WorkerError, "policy"):
            CodingJobPayload.from_dict(
                {"task": "fix", "criteria": ["one"], "policy": "automatic"}
            )

    def test_lease_keeper_heartbeats_and_reports_loss(self) -> None:
        queue = FakeHeartbeatQueue()
        keeper = LeaseKeeper(
            queue,
            "job-1",
            worker_id="worker",
            lease_seconds=0.06,
        )
        with keeper:
            time.sleep(0.09)
        self.assertGreaterEqual(queue.calls, 1)
        self.assertIsNone(keeper.lost_error)

        failing_queue = FakeHeartbeatQueue(fail=True)
        failing = LeaseKeeper(
            failing_queue,
            "job-2",
            worker_id="worker",
            lease_seconds=0.06,
        )
        with failing:
            time.sleep(0.09)
        self.assertIsInstance(failing.lost_error, RuntimeError)


if __name__ == "__main__":
    unittest.main()
