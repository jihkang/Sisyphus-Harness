from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import unittest

from sisyphus_harness.contracts import (
    AgentResult,
    AgentTask,
    CadencePolicy,
    CandidatePolicy,
    CommandResult,
    EvaluationAggregate,
    EvaluationObservation,
    VerificationReceipt,
    WireModel,
    WorkspaceSnapshot,
    to_wire,
)
from sisyphus_harness.models import JobRecord, JobStatus
from sisyphus_harness.provider import ChatMessage
from sisyphus_harness.tools import ToolOutcome


class ContractCodecTests(unittest.TestCase):
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
                "summary": "done",
                "schema_version": "sisyphus_harness.agent_run.v1",
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


if __name__ == "__main__":
    unittest.main()
