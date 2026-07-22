from __future__ import annotations

from collections import deque
from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from sisyphus_harness.authority import (
    authority_database_path,
    evolution_artifact_root,
    policy_root,
)
from sisyphus_harness.cli import main
from sisyphus_harness.config import CadencePolicy
from sisyphus_harness.contracts.control import TaskOutcomeDecision
from sisyphus_harness.contracts.verification_service import VerificationProfile
from sisyphus_harness.evolution import CandidatePolicy
from sisyphus_harness.provider import ChatResponse
from sisyphus_harness.queue import JobQueue

from .helpers import create_git_repo, run_git
from .test_control_outcomes import _attempt
from .test_evidence_adjudication import _contract, _profile


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = deque(responses)

    def complete(self, messages) -> ChatResponse:
        return ChatResponse(content=self.responses.popleft())


def action(tool: str, arguments: dict[str, object]) -> str:
    return json.dumps(
        {
            "type": "tool",
            "tool": tool,
            "arguments": arguments,
            "reason": "CLI test",
        }
    )


def invoke(arguments: list[str]) -> tuple[int, object, object]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(arguments)
    output = json.loads(stdout.getvalue()) if stdout.getvalue() else None
    error = json.loads(stderr.getvalue()) if stderr.getvalue() else None
    return code, output, error


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.repository = create_git_repo(self.root / "repository")

    def write_full_config(self, *, verify_code: str = "pass") -> Path:
        path = self.repository / "sisyphus-harness.toml"
        path.write_text(
            textwrap.dedent(
                f"""
                [provider]
                base_url = "http://127.0.0.1:8080/v1"
                model = "local"

                [agent]
                max_steps = 8
                max_runtime_seconds = 120
                max_file_bytes = 4096
                max_tool_output_chars = 4000
                max_protocol_errors = 1
                max_compactions = 2

                [execution]
                trust_mode = "trusted-in-process"

                [cadence]
                compaction_interval_steps = 4
                context_char_limit = 4000
                keep_recent_events = 2
                reflection_interval_steps = 2
                observation_interval_steps = 2
                verification_interval_mutations = 2
                stagnation_limit = 4

                [evolution]
                max_metric_calls = 2
                max_candidate_proposals = 1
                seed = 0
                min_train_delta = 0.01
                min_holdout_delta = 0.01

                [commands.behavior]
                argv = {json.dumps([sys.executable, "-c", verify_code])}
                timeout_seconds = 10
                criteria = ["behavior passes"]

                [verify]
                commands = ["behavior"]
                """
            ),
            encoding="utf-8",
        )
        return path

    def commit_all(self, message: str) -> None:
        run_git(self.repository, "add", "--all")
        run_git(self.repository, "commit", "-q", "-m", message)

    def test_init_and_queue_lifecycle_commands(self) -> None:
        code, initialized, error = invoke(
            ["init", "--repo", str(self.repository)]
        )
        self.assertEqual(code, 0)
        self.assertIsNone(error)
        self.assertEqual(initialized["status"], "initialized")

        code, queued, _ = invoke(
            [
                "queue-enqueue",
                "--repo",
                str(self.repository),
                "--kind",
                "coding-agent",
                "--idempotency-key",
                "request-1",
                "--payload-json",
                '{"task":"fix"}',
            ]
        )
        self.assertEqual(code, 0)
        job_id = queued["job_id"]

        code, claimed, _ = invoke(
            [
                "queue-claim",
                "--repo",
                str(self.repository),
                "--worker-id",
                "worker-1",
                "--lease-seconds",
                "60",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(claimed["job_id"], job_id)

        code, renewed, _ = invoke(
            [
                "queue-heartbeat",
                "--repo",
                str(self.repository),
                "--job-id",
                job_id,
                "--worker-id",
                "worker-1",
                "--lease-seconds",
                "60",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(renewed["lease_owner"], "worker-1")

        code, completed, _ = invoke(
            [
                "queue-finish",
                "--repo",
                str(self.repository),
                "--job-id",
                job_id,
                "--worker-id",
                "worker-1",
                "--status",
                "completed",
                "--result-json",
                '{"success":true}',
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(completed["status"], "completed")

        code, fetched, _ = invoke(
            [
                "queue-get",
                "--repo",
                str(self.repository),
                "--job-id",
                job_id,
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(fetched["result"], {"success": True})

        code, empty, _ = invoke(
            [
                "queue-claim",
                "--repo",
                str(self.repository),
                "--worker-id",
                "worker-2",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(empty, {"job": None})

    def test_verifier_asset_and_profile_commands_publish_bound_v2_contracts(self) -> None:
        config = self.write_full_config()
        assets = self.repository / "operator-verifier"
        assets.mkdir()
        (assets / "hidden_check.py").write_text(
            "from module import VALUE\nassert VALUE == 1\n",
            encoding="utf-8",
        )

        code, reference, error = invoke(
            [
                "verifier-assets-create",
                "--repo",
                str(self.repository),
                "--source",
                "operator-verifier",
            ]
        )

        self.assertEqual(code, 0)
        self.assertIsNone(error)
        self.assertTrue(reference["bundle_id"].startswith("verifier-assets:sha256:"))

        code, profile_payload, error = invoke(
            [
                "verification-profile-create",
                "--repo",
                str(self.repository),
                "--config",
                config.name,
                "--profile-id",
                "control-final",
                "--asset-bundle-id",
                reference["bundle_id"],
            ]
        )

        self.assertEqual(code, 0)
        self.assertIsNone(error)
        profile = VerificationProfile.from_dict(profile_payload)
        self.assertEqual(
            profile.schema_version,
            "sisyphus_harness.verification_profile.v2",
        )
        self.assertEqual(profile.asset_bundle.bundle_id, reference["bundle_id"])
        self.assertEqual(profile.commands[0].name, "behavior")

    def test_queue_failure_and_invalid_payload_return_structured_results(self) -> None:
        _, queued, _ = invoke(
            [
                "queue-enqueue",
                "--repo",
                str(self.repository),
                "--kind",
                "coding-agent",
                "--idempotency-key",
                "request-2",
                "--payload-json",
                '{"task":"fix"}',
            ]
        )
        _, claimed, _ = invoke(
            [
                "queue-claim",
                "--repo",
                str(self.repository),
                "--worker-id",
                "worker",
            ]
        )
        code, failed, _ = invoke(
            [
                "queue-finish",
                "--repo",
                str(self.repository),
                "--job-id",
                claimed["job_id"],
                "--worker-id",
                "worker",
                "--status",
                "failed",
                "--result-json",
                '{"error":"failed"}',
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["job_id"], queued["job_id"])

        code, output, error = invoke(
            [
                "queue-enqueue",
                "--repo",
                str(self.repository),
                "--kind",
                "bad",
                "--idempotency-key",
                "bad",
                "--payload-json",
                "[]",
            ]
        )
        self.assertEqual(code, 2)
        self.assertIsNone(output)
        self.assertEqual(error["error_type"], "ValueError")

        code, missing, _ = invoke(
            [
                "queue-get",
                "--repo",
                str(self.repository),
                "--job-id",
                "missing",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(missing, {"job": None})

    def test_verify_supports_full_config_and_returns_command_status(self) -> None:
        self.write_full_config()
        self.commit_all("add passing config")

        code, receipt, error = invoke(
            ["verify", "--repo", str(self.repository)]
        )

        self.assertEqual(code, 0)
        self.assertIsNone(error)
        self.assertTrue(receipt["passed"])

        failing = self.repository / "failing.toml"
        failing.write_text(
            textwrap.dedent(
                f"""
                [commands.fail]
                argv = {json.dumps([sys.executable, "-c", "raise SystemExit(1)"])}
                timeout_seconds = 10
                criteria = ["failure is reported"]

                [verify]
                commands = ["fail"]
                """
            ),
            encoding="utf-8",
        )
        self.commit_all("add failing config")
        code, receipt, _ = invoke(
            [
                "verify",
                "--repo",
                str(self.repository),
                "--config",
                "failing.toml",
                "--trusted-in-process",
            ]
        )
        self.assertEqual(code, 1)
        self.assertFalse(receipt["passed"])

    def test_agent_run_uses_fake_provider_and_real_verifier(self) -> None:
        original = "def add(left, right):\n    return left - right\n"
        (self.repository / "calc.py").write_text(original, encoding="utf-8")
        self.write_full_config(
            verify_code="from calc import add; assert add(2, 3) == 5"
        )
        self.commit_all("add agent fixture")
        expected_hash = (
            "sha256:" + hashlib.sha256(original.encode("utf-8")).hexdigest()
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

        with patch(
            "sisyphus_harness.interfaces.cli.handlers.execution."
            "OpenAICompatibleProvider",
            return_value=provider,
        ):
            code, result, error = invoke(
                [
                    "agent-run",
                    "--repo",
                    str(self.repository),
                    "--task",
                    "Fix add.",
                    "--criterion",
                    "behavior passes",
                    "--run-id",
                    "cli-agent",
                ]
            )

        self.assertEqual(code, 0, msg=result)
        self.assertIsNone(error)
        self.assertTrue(result["success"])
        self.assertEqual(result["verifications"], 1)
        self.assertIn(
            "return left + right",
            (self.repository / "calc.py").read_text(encoding="utf-8"),
        )

    def test_benchmark_run_dispatches_real_evaluator(self) -> None:
        self.write_full_config()
        case_dir = self.repository / "benchmarks" / "case"
        workspace = case_dir / "workspace"
        workspace.mkdir(parents=True)
        original = "def add(left, right):\n    return left - right\n"
        (workspace / "calc.py").write_text(original, encoding="utf-8")
        (case_dir / "verify.py").write_text(
            "import os, sys\nsys.path.insert(0, os.getcwd())\n"
            "from calc import add\nassert add(2, 3) == 5\n",
            encoding="utf-8",
        )
        (case_dir / "case.json").write_text(
            json.dumps(
                {
                    "schema_version": "sisyphus_harness.benchmark_case.v1",
                    "id": "cli-case",
                    "instruction": "Fix add.",
                    "acceptance_criteria": ["add returns sums"],
                    "workspace": "workspace",
                    "verifiers": [
                        {
                            "name": "correct-sums",
                            "criterion": "add returns sums",
                            "script": "verify.py",
                        }
                    ],
                    "timeout_seconds": 10,
                }
            ),
            encoding="utf-8",
        )
        dataset = self.repository / "benchmarks" / "dataset.json"
        dataset.write_text(
            json.dumps(
                {
                    "schema_version": "sisyphus_harness.benchmark_dataset.v1",
                    "cases": ["case"],
                }
            ),
            encoding="utf-8",
        )
        self.commit_all("add benchmark")
        expected_hash = (
            "sha256:" + hashlib.sha256(original.encode("utf-8")).hexdigest()
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

        with patch(
            "sisyphus_harness.interfaces.cli.handlers.execution."
            "OpenAICompatibleProvider",
            return_value=provider,
        ):
            code, result, _ = invoke(
                [
                    "benchmark-run",
                    "--repo",
                    str(self.repository),
                    "--dataset",
                    "benchmarks/dataset.json",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(result["evaluation"]["success_rate"], 1.0)

    def test_evolve_dispatch_and_policy_approval_activation(self) -> None:
        self.write_full_config()
        benchmark_root = Path(__file__).resolve().parents[1] / "benchmarks"
        local_benchmarks = self.repository / "benchmarks"
        local_benchmarks.mkdir()
        train = local_benchmarks / "train.json"
        holdout = local_benchmarks / "holdout.json"
        source_case = benchmark_root / "cases" / "python-add"
        target_case = local_benchmarks / "case"
        import shutil

        shutil.copytree(source_case, target_case)
        dataset_payload = {
            "schema_version": "sisyphus_harness.benchmark_dataset.v1",
            "cases": ["case"],
        }
        train.write_text(json.dumps(dataset_payload), encoding="utf-8")
        holdout.write_text(json.dumps(dataset_payload), encoding="utf-8")
        self.commit_all("add evolution data")
        candidate = CandidatePolicy(
            strategy_prompt="Inspect hashes before editing.",
            cadence=CadencePolicy(),
        )

        class FakeResult:
            accepted = True

            def to_dict(self):
                return {"accepted": True, "candidate": candidate.to_dict()}

        class FakeRunner:
            def __init__(self, **kwargs) -> None:
                pass

            def run(self, **kwargs):
                return FakeResult()

        with patch(
            "sisyphus_harness.interfaces.cli.handlers.execution.EvolutionRunner",
            FakeRunner,
        ):
            code, result, _ = invoke(
                [
                    "evolve",
                    "--repo",
                    str(self.repository),
                    "--train-dataset",
                    "benchmarks/train.json",
                    "--holdout-dataset",
                    "benchmarks/holdout.json",
                    "--evolution-id",
                    "cli-evolution",
                ]
            )
        self.assertEqual(code, 0)
        self.assertTrue(result["accepted"])

        evolution_dir = evolution_artifact_root(self.repository) / "approved-evolution"
        evolution_dir.mkdir(parents=True)
        (evolution_dir / "result.json").write_text(
            json.dumps(
                {
                    "schema_version": "sisyphus_harness.evolution_result.v1",
                    "evolution_id": "approved-evolution",
                    "accepted": True,
                    "status": "proposed",
                    "candidate": candidate.to_dict(),
                }
            ),
            encoding="utf-8",
        )
        code, approval_result, _ = invoke(
            [
                "policy-approve",
                "--repo",
                str(self.repository),
                "--evolution-id",
                "approved-evolution",
                "--note",
                "reviewed",
            ]
        )
        self.assertEqual(code, 0)

        code, active_result, _ = invoke(
            [
                "policy-activate",
                "--repo",
                str(self.repository),
                "--evolution-id",
                "approved-evolution",
                "--approval",
                approval_result["approval_path"],
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(active_result["status"], "activated")

        code, shown, _ = invoke(
            ["policy-show", "--repo", str(self.repository)]
        )
        self.assertEqual(code, 0)
        self.assertEqual(shown["candidate_hash"], candidate.candidate_hash)

    def test_evolve_rejects_unsafe_id_before_rollout_construction(self) -> None:
        self.write_full_config()
        with (
            patch(
                "sisyphus_harness.interfaces.cli.handlers.execution."
                "load_benchmark_dataset",
                side_effect=[[{"id": "train"}], [{"id": "holdout"}]],
            ),
            patch(
                "sisyphus_harness.interfaces.cli.handlers.execution."
                "CodingAgentBenchmarkEvaluator"
            ) as evaluator,
        ):
            code, _, error = invoke(
                [
                    "evolve",
                    "--repo",
                    str(self.repository),
                    "--train-dataset",
                    "train.json",
                    "--holdout-dataset",
                    "holdout.json",
                    "--evolution-id",
                    "../escape",
                ]
            )

        self.assertEqual(code, 2)
        self.assertIn("unsafe", error["error"])
        evaluator.assert_not_called()

    def test_policy_rejects_unsafe_id_before_registry_creation(self) -> None:
        registry_root = policy_root(self.repository)

        code, _, error = invoke(
            [
                "policy-approve",
                "--repo",
                str(self.repository),
                "--evolution-id",
                "../escape",
            ]
        )

        self.assertEqual(code, 2)
        self.assertIn("unsafe", error["error"])
        self.assertFalse(registry_root.exists())

    def test_policy_show_without_active_and_missing_active_selection(self) -> None:
        self.write_full_config()
        self.commit_all("add config")
        code, shown, _ = invoke(
            ["policy-show", "--repo", str(self.repository)]
        )
        self.assertEqual(code, 0)
        self.assertEqual(shown, {"policy": None})

        code, _, error = invoke(
            [
                "agent-run",
                "--repo",
                str(self.repository),
                "--task",
                "No-op",
                "--criterion",
                "passes",
                "--policy",
                "active",
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("no active", error["error"])

    def test_task_submit_and_worker_once_dispatch(self) -> None:
        self.write_full_config()
        self.commit_all("add queued worker config")
        code, queued, _ = invoke(
            [
                "task-submit",
                "--repo",
                str(self.repository),
                "--task",
                "Run the task.",
                "--criterion",
                "it passes",
                "--idempotency-key",
                "queued-task",
                "--run-id",
                "queued-run",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(queued["kind"], "coding-agent")
        self.assertRegex(queued["payload"]["config_sha256"], r"^sha256:[0-9a-f]{64}$")
        self.assertIn("workspace_bundle", queued["payload"])
        self.assertIn("candidate_hash", queued["payload"]["policy_snapshot"])

        class FakeWorker:
            def __init__(self, repo_root) -> None:
                pass

            def run_once(self, **kwargs):
                from sisyphus_harness.models import JobRecord, JobStatus

                return JobRecord(
                    job_id="job",
                    idempotency_key="key",
                    kind="coding-agent",
                    payload={},
                    status=JobStatus.COMPLETED,
                    lease_owner=None,
                    lease_expires_at=None,
                    attempts=1,
                    result={"success": True},
                    created_at="now",
                    updated_at="now",
                )

        with patch(
            "sisyphus_harness.interfaces.cli.handlers.task.CodingWorker",
            FakeWorker,
        ):
            code, completed, _ = invoke(
                [
                    "worker-once",
                    "--repo",
                    str(self.repository),
                    "--worker-id",
                    "worker",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(completed["status"], "completed")

    def test_task_status_and_control_adjudication_are_separate(self) -> None:
        self.write_full_config()
        profile = _profile()
        contract = _contract(profile)
        (self.repository / "profile.json").write_text(
            json.dumps(profile.to_dict()),
            encoding="utf-8",
        )
        (self.repository / "contract.json").write_text(
            json.dumps(contract.to_dict()),
            encoding="utf-8",
        )
        self.commit_all("add Control inputs")
        queue = JobQueue(authority_database_path(self.repository))
        queued = queue.enqueue(
            kind="coding-agent",
            payload={"task": "fix"},
            idempotency_key="control-task",
        )
        claimed = queue.claim(
            worker_id="worker",
            lease_seconds=30,
            now=100,
        )
        assert claimed is not None
        attempt = _attempt(queued.job_id, 1, agent_success=True)
        queue.finish_attempt(
            queued.job_id,
            worker_id="worker",
            attempt=attempt,
            now=101,
        )

        code, status, error = invoke(
            [
                "task-status",
                "--repo",
                str(self.repository),
                "--job-id",
                queued.job_id,
            ]
        )
        self.assertEqual(code, 0)
        self.assertIsNone(error)
        self.assertEqual(status["job"]["status"], "completed")
        self.assertEqual(
            status["attempt_finished"]["attempt_digest"],
            attempt.attempt_digest,
        )
        self.assertIsNone(status["task_outcome"])

        calls = []

        class PublishedOutcome:
            decision = TaskOutcomeDecision.PASSED

            def to_dict(self):
                return {"decision": self.decision.value, "job_id": queued.job_id}

        class FakeControlService:
            def adjudicate(self, request):
                calls.append(request)
                return PublishedOutcome()

        with patch(
            "sisyphus_harness.interfaces.cli.handlers.task."
            "build_control_task_outcome_service",
            return_value=FakeControlService(),
        ):
            code, outcome, error = invoke(
                [
                    "task-adjudicate",
                    "--repo",
                    str(self.repository),
                    "--job-id",
                    queued.job_id,
                    "--profile",
                    "profile.json",
                    "--contract",
                    "contract.json",
                    "--run-id",
                    "control-final-1",
                ]
            )
        self.assertEqual(code, 0)
        self.assertIsNone(error)
        self.assertEqual(outcome["decision"], "passed")
        self.assertEqual(calls[0].job_id, queued.job_id)


if __name__ == "__main__":
    unittest.main()
