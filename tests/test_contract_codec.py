from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import unittest

from sisyphus_harness.contracts import (
    AgentResult,
    AgentTask,
    ArtifactRef,
    CadencePolicy,
    CandidatePolicy,
    CommandResult,
    CommandSpec,
    EvaluationAggregate,
    EvaluationObservation,
    VerificationReceipt,
    VerificationProfile,
    VerificationRequest,
    WireModel,
    loads_strict_json,
    WorkspaceSnapshot,
    to_wire,
)
from sisyphus_harness.models import JobRecord, JobStatus
from sisyphus_harness.provider import ChatMessage
from sisyphus_harness.tools import ToolOutcome


class ContractCodecTests(unittest.TestCase):
    def test_verification_contracts_reject_mutable_sequence_fields(self) -> None:
        class StatefulTuple(tuple):
            def __iter__(self):
                raise AssertionError("tuple subclass must not be iterated")

        with self.assertRaisesRegex(ValueError, "immutable tuple"):
            CommandSpec(
                name="tests",
                argv=["python"],  # type: ignore[arg-type]
                timeout_seconds=1,
                criteria=("passes",),
            )
        with self.assertRaisesRegex(ValueError, "immutable tuple"):
            CommandSpec(
                name="tests",
                argv=("python",),
                timeout_seconds=1,
                criteria=["passes"],  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ValueError, "immutable tuple"):
            CommandSpec(
                name="tests",
                argv=StatefulTuple(("python",)),
                timeout_seconds=1,
                criteria=("passes",),
            )

        spec = CommandSpec("tests", ("python",), 1, ("passes",))
        with self.assertRaisesRegex(ValueError, "immutable tuple"):
            VerificationProfile("profile", [spec])  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "immutable tuple"):
            VerificationProfile("profile", StatefulTuple((spec,)))
        with self.assertRaisesRegex(ValueError, "immutable tuple"):
            VerificationRequest(
                "run",
                "/workspace",
                "state",
                [spec],  # type: ignore[arg-type]
            )

        result_values = {
            "name": "tests",
            "argv": ("python",),
            "criteria": ("passes",),
            "passed": True,
            "timed_out": False,
            "exit_code": 0,
            "duration_ms": 1,
            "executable_path": "/usr/bin/python",
            "executable_sha256": "sha256:" + "1" * 64,
            "stdout_path": "stdout.txt",
            "stderr_path": "stderr.txt",
            "workspace_state_before": "state",
            "workspace_state_after": "state",
            "workspace_unchanged": True,
        }
        with self.assertRaisesRegex(ValueError, "immutable tuple"):
            CommandResult(**(result_values | {"argv": ["python"]}))  # type: ignore[arg-type]
        result = CommandResult(**result_values)
        with self.assertRaisesRegex(ValueError, "immutable command tuple"):
            VerificationReceipt(
                run_id="run",
                workspace="/workspace",
                worktree_commit_sha="a" * 40,
                started_at="start",
                finished_at="finish",
                passed=True,
                commands=[result],  # type: ignore[arg-type]
                workspace_state_before="state",
                workspace_state_after="state",
                workspace_unchanged=True,
            )

    def test_plain_contract_uses_one_recursive_encoder(self) -> None:
        result = AgentResult(
            run_id="agent-1",
            success=True,
            reason="verified",
            steps=3,
            compactions=1,
            verifications=1,
            workspace_state_before="before",
            workspace_state_after="after",
            changed_paths=("a.py", "b.py"),
            artifact_path="artifacts/agent-1",
            summary="done",
        )

        self.assertEqual(
            result.to_dict(),
            {
                "run_id": "agent-1",
                "success": True,
                "reason": "verified",
                "steps": 3,
                "compactions": 1,
                "verifications": 1,
                "workspace_state_before": "before",
                "workspace_state_after": "after",
                "changed_paths": ["a.py", "b.py"],
                "artifact_path": "artifacts/agent-1",
                "verification_artifacts": [],
                "summary": "done",
                "schema_version": "sisyphus_harness.agent_run.v2",
            },
        )

    def test_encoder_handles_nested_models_mappings_tuples_and_enums(self) -> None:
        job = JobRecord(
            job_id="job-1",
            idempotency_key="request-1",
            kind="coding-agent",
            payload={"criteria": ("one", "two")},
            status=JobStatus.RUNNING,
            lease_owner="worker-1",
            lease_expires_at=10.0,
            attempts=1,
            result=None,
            created_at="created",
            updated_at="updated",
        )

        payload = job.to_dict()

        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["payload"], {"criteria": ["one", "two"]})

    def test_migrated_plain_models_keep_json_compatible_shapes(self) -> None:
        observation = EvaluationObservation(
            score=0.5,
            success=False,
            hard_gate_passed=True,
            diagnostics={"events": ["read", "write"]},
            scores={"correctness": 0.5},
        )
        cases = (
            (
                AgentTask("fix task", ("tests pass",)),
                {
                    "instruction": "fix task",
                    "acceptance_criteria": ["tests pass"],
                },
            ),
            (
                ChatMessage("user", "inspect"),
                {"role": "user", "content": "inspect"},
            ),
            (
                ToolOutcome("read_file", {"path": "a.py"}, False),
                {
                    "tool": "read_file",
                    "output": {"path": "a.py"},
                    "mutated": False,
                },
            ),
            (
                WorkspaceSnapshot("a" * 40, "sha256:" + "b" * 64, ("a.py",)),
                {
                    "commit_sha": "a" * 40,
                    "state_hash": "sha256:" + "b" * 64,
                    "changed_paths": ["a.py"],
                },
            ),
            (
                observation,
                {
                    "score": 0.5,
                    "success": False,
                    "hard_gate_passed": True,
                    "diagnostics": {"events": ["read", "write"]},
                    "scores": {"correctness": 0.5},
                },
            ),
            (
                EvaluationAggregate(1, 0.5, 0.0, True, (observation.to_dict(),)),
                {
                    "count": 1,
                    "mean_score": 0.5,
                    "success_rate": 0.0,
                    "all_hard_gates_passed": True,
                    "observations": [observation.to_dict()],
                },
            ),
        )

        for model, expected in cases:
            with self.subTest(model=type(model).__name__):
                self.assertEqual(model.to_dict(), expected)

    def test_only_computed_receipt_and_candidate_fields_are_overridden(self) -> None:
        command = CommandResult(
            name="tests",
            argv=("python", "-m", "unittest"),
            criteria=("suite passes",),
            passed=True,
            timed_out=False,
            exit_code=0,
            duration_ms=12,
            executable_path="/usr/bin/python",
            executable_sha256="sha256:" + "1" * 64,
            stdout_path="stdout.txt",
            stderr_path="stderr.txt",
            workspace_state_before="state",
            workspace_state_after="state",
            workspace_unchanged=True,
        )
        receipt = VerificationReceipt(
            run_id="verify-1",
            workspace="workspace",
            worktree_commit_sha="a" * 40,
            started_at="start",
            finished_at="finish",
            passed=True,
            commands=(command,),
            workspace_state_before="state",
            workspace_state_after="state",
            workspace_unchanged=True,
        )
        candidate = CandidatePolicy(
            strategy_prompt="Inspect before editing.",
            cadence=CadencePolicy(),
        )

        self.assertEqual(
            receipt.to_dict()["criteria"],
            [
                {
                    "criterion": "suite passes",
                    "command_name": "tests",
                    "passed": True,
                }
            ],
        )
        self.assertEqual(
            candidate.to_dict()["candidate_hash"],
            candidate.candidate_hash,
        )

    def test_unsupported_values_and_non_string_mapping_keys_fail_closed(self) -> None:
        @dataclass(frozen=True, slots=True)
        class PathModel(WireModel):
            path: Path

        with self.assertRaisesRegex(TypeError, "unsupported wire value"):
            PathModel(Path("workspace")).to_dict()
        with self.assertRaisesRegex(TypeError, "string keys"):
            to_wire({1: "invalid"})
        with self.assertRaisesRegex(TypeError, "finite"):
            to_wire(float("nan"))

    def test_strict_json_rejects_duplicate_fields_and_non_finite_numbers(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate field"):
            loads_strict_json('{"run_id":"one","run_id":"two"}')
        with self.assertRaisesRegex(ValueError, "non-finite"):
            loads_strict_json('{"score":NaN}')

    def test_artifact_and_request_contracts_round_trip_strictly(self) -> None:
        artifact = ArtifactRef(
            artifact_id="verify-1/receipt.json",
            sha256="sha256:" + "1" * 64,
            size_bytes=128,
            media_type="application/json",
        )
        self.assertEqual(ArtifactRef.from_dict(artifact.to_dict()), artifact)
        with self.assertRaisesRegex(ValueError, "safe relative"):
            ArtifactRef(
                artifact_id="../receipt.json",
                sha256="sha256:" + "1" * 64,
                size_bytes=128,
                media_type="application/json",
            )

        request = VerificationRequest(
            run_id="verify-1",
            workspace="/workspace",
            workspace_state_before="sha256:" + "2" * 64,
            commands=(
                CommandSpec(
                    name="tests",
                    argv=("python", "-m", "unittest"),
                    timeout_seconds=10,
                    criteria=("suite passes",),
                ),
            ),
        )
        self.assertEqual(VerificationRequest.from_dict(request.to_dict()), request)
        tampered = request.to_dict()
        tampered["workspace_state_before"] = "sha256:" + "3" * 64
        with self.assertRaisesRegex(ValueError, "digest does not match"):
            VerificationRequest.from_dict(tampered)

    def test_receipt_parser_rejects_tampering_and_unknown_fields(self) -> None:
        command = CommandResult(
            name="tests",
            argv=("python", "-m", "unittest"),
            criteria=("suite passes",),
            passed=True,
            timed_out=False,
            exit_code=0,
            duration_ms=12,
            executable_path="/usr/bin/python",
            executable_sha256="sha256:" + "1" * 64,
            stdout_path="00-tests/stdout.txt",
            stderr_path="00-tests/stderr.txt",
            workspace_state_before="state",
            workspace_state_after="state",
            workspace_unchanged=True,
        )
        receipt = VerificationReceipt(
            run_id="verify-1",
            workspace="/workspace",
            worktree_commit_sha="a" * 40,
            started_at="start",
            finished_at="finish",
            passed=True,
            commands=(command,),
            workspace_state_before="state",
            workspace_state_after="state",
            workspace_unchanged=True,
            request_digest="sha256:" + "2" * 64,
        )
        self.assertEqual(VerificationReceipt.from_dict(receipt.to_dict()), receipt)

        tampered = receipt.to_dict()
        tampered["passed"] = False
        with self.assertRaisesRegex(ValueError, "inconsistent|digest"):
            VerificationReceipt.from_dict(tampered)
        unknown = receipt.to_dict()
        unknown["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            VerificationReceipt.from_dict(unknown)


if __name__ == "__main__":
    unittest.main()
