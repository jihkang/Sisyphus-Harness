from __future__ import annotations

from collections import deque
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from sisyphus_harness.agent import AgentTask, LocalCodingAgent
from sisyphus_harness.config import AgentLimits, CadencePolicy
from sisyphus_harness.models import CommandSpec
from sisyphus_harness.provider import ChatMessage, ChatResponse, ProviderError
from sisyphus_harness.verifier import BoundedVerifier

from .helpers import create_git_repo, run_git


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = deque(responses)
        self.messages: list[tuple[ChatMessage, ...]] = []

    def complete(self, messages: tuple[ChatMessage, ...]) -> ChatResponse:
        self.messages.append(messages)
        if not self.responses:
            raise AssertionError("fake provider response queue is empty")
        return ChatResponse(
            content=self.responses.popleft(),
            prompt_tokens=100,
            completion_tokens=20,
        )

class ErrorProvider:
    def complete(self, messages: tuple[ChatMessage, ...]) -> ChatResponse:
        raise ProviderError("local model unavailable")


def decision(tool: str, arguments: dict[str, object]) -> str:
    return json.dumps(
        {
            "type": "tool",
            "tool": tool,
            "arguments": arguments,
            "reason": "test action",
        }
    )


def finish(summary: str = "implemented") -> str:
    return json.dumps({"type": "finish", "summary": summary})


def sha256(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


class AgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        root = Path(self.temporary_directory.name)
        self.repository = create_git_repo(root / "repository")
        (self.repository / "calc.py").write_text(
            "def add(left, right):\n    return left - right\n",
            encoding="utf-8",
        )
        run_git(self.repository, "add", "calc.py")
        run_git(self.repository, "commit", "-q", "-m", "add fixture")
        self.agent_artifacts = root / "agent-artifacts"
        self.verification_artifacts = root / "verification-artifacts"
        self.command = CommandSpec(
            name="behavior",
            argv=(
                sys.executable,
                "-c",
                "from calc import add; assert add(2, 3) == 5",
            ),
            timeout_seconds=10,
            criteria=(
                "add returns the sum",
                "add(2, 3) returns 5",
                "tests pass",
            ),
        )

    def build_agent(
        self,
        provider: FakeProvider,
        *,
        cadence: CadencePolicy | None = None,
        limits: AgentLimits | None = None,
    ) -> LocalCodingAgent:
        return LocalCodingAgent(
            provider=provider,
            verifier=BoundedVerifier(self.verification_artifacts),
            agent_artifact_root=self.agent_artifacts,
            limits=limits or AgentLimits(max_steps=12),
            cadence=cadence or CadencePolicy(),
            strategy_prompt="Inspect, edit narrowly, and verify.",
        )

    def test_agent_edits_then_requires_final_verification(self) -> None:
        original = "def add(left, right):\n    return left - right\n"
        provider = FakeProvider(
            [
                decision("list_files", {}),
                decision("read_file", {"path": "calc.py"}),
                decision(
                    "replace_text",
                    {
                        "path": "calc.py",
                        "old": "return left - right",
                        "new": "return left + right",
                        "expected_sha256": sha256(original),
                    },
                ),
                finish(),
            ]
        )
        cadence = CadencePolicy(
            compaction_interval_steps=2,
            context_char_limit=4000,
            keep_recent_events=1,
            reflection_interval_steps=2,
            observation_interval_steps=2,
            verification_interval_mutations=2,
            stagnation_limit=4,
        )

        result = self.build_agent(provider, cadence=cadence).run(
            self.repository,
            AgentTask("Fix add().", ("add(2, 3) returns 5",)),
            (self.command,),
            run_id="successful-agent",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.reason, "final verification passed")
        self.assertEqual(result.verifications, 1)
        self.assertGreaterEqual(result.compactions, 1)
        self.assertIn("calc.py", result.changed_paths)
        self.assertIn(
            "return left + right",
            (self.repository / "calc.py").read_text(encoding="utf-8"),
        )
        initial_context = json.loads(provider.messages[0][1].content)
        initial_observation = initial_context["workspace_observation"]
        self.assertEqual(initial_observation["files"], ["calc.py", "tracked.txt"])
        self.assertEqual(initial_observation["file_count"], 2)
        self.assertFalse(initial_observation["files_truncated"])
        self.assertNotIn("state_hash", initial_observation)
        edit_context = json.loads(provider.messages[2][1].content)
        finish_context = json.loads(provider.messages[3][1].content)
        self.assertEqual(
            edit_context["known_file_hashes"],
            {"calc.py": sha256(original)},
        )
        self.assertNotIn("content", edit_context["working_file"])
        self.assertEqual(edit_context["working_file"]["content_message_index"], 2)
        self.assertIn(original, provider.messages[2][2].content)
        self.assertEqual(
            finish_context["known_file_hashes"]["calc.py"],
            sha256("def add(left, right):\n    return left + right\n"),
        )
        self.assertIn("return left + right", provider.messages[3][2].content)
        run_root = self.agent_artifacts / "successful-agent"
        self.assertTrue((run_root / "result.json").is_file())
        self.assertEqual(len(list((run_root / "steps").glob("*.json"))), 4)
        compact_payload = json.loads(
            next((run_root / "compactions").glob("*.json")).read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("compact_summary", provider.messages[-1][1].content)
        self.assertGreater(compact_payload["summary"]["compacted_event_count"], 0)

    def test_working_file_content_is_sent_verbatim_outside_json(self) -> None:
        content = "import re\nPATTERN = r'[\\s_]+'\n"
        messages = self.build_agent(FakeProvider([]))._messages(
            AgentTask("Fix pattern.", ("pattern is correct",)),
            [],
            None,
            None,
            False,
            {"pattern.py": sha256(content)},
            {
                "path": "pattern.py",
                "sha256": sha256(content),
                "content": content,
                "content_truncated": False,
            },
            {"pattern is correct": "not_run"},
        )

        context = json.loads(messages[1].content)
        self.assertNotIn("content", context["working_file"])
        self.assertEqual(context["working_file"]["content_format"], "verbatim")
        self.assertIn("PATTERN = r'[\\s_]+'", messages[2].content)

    def test_failed_finish_is_returned_to_model_for_repair(self) -> None:
        original = "def add(left, right):\n    return left - right\n"
        wrong = "def add(left, right):\n    return left * right\n"
        provider = FakeProvider(
            [
                decision("read_file", {"path": "calc.py"}),
                decision(
                    "replace_text",
                    {
                        "path": "calc.py",
                        "old": "return left - right",
                        "new": "return left * right",
                        "expected_sha256": sha256(original),
                    },
                ),
                finish("first attempt"),
                decision("read_file", {"path": "calc.py"}),
                decision(
                    "replace_text",
                    {
                        "path": "calc.py",
                        "old": "return left * right",
                        "new": "return left + right",
                        "expected_sha256": sha256(wrong),
                    },
                ),
                finish("repaired"),
            ]
        )

        result = self.build_agent(provider).run(
            self.repository,
            AgentTask("Fix add().", ("add(2, 3) returns 5",)),
            (self.command,),
            run_id="repair-agent",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.verifications, 2)
        fourth_context = json.loads(provider.messages[3][1].content)
        self.assertEqual(
            fourth_context["criterion_status"]["add returns the sum"],
            "failed",
        )
        self.assertEqual(
            fourth_context["failed_criteria"],
            [
                "add(2, 3) returns 5",
                "add returns the sum",
                "tests pass",
            ],
        )
        failed_verification = next(
            event
            for event in fourth_context["recent_events"]
            if event.get("kind") == "verification" and not event.get("passed")
        )
        self.assertIn("make a concrete repair", failed_verification["commands"][0]["feedback"])
        self.assertNotIn("stderr_excerpt", failed_verification["commands"][0])

    def test_repeated_failed_finish_requires_a_workspace_change(self) -> None:
        original = "def add(left, right):\n    return left - right\n"
        provider = FakeProvider(
            [
                finish("first attempt"),
                finish("same state"),
                decision("read_file", {"path": "calc.py"}),
                decision(
                    "replace_text",
                    {
                        "path": "calc.py",
                        "old": "return left - right",
                        "new": "return left + right",
                        "expected_sha256": sha256(original),
                    },
                ),
                finish("repaired"),
            ]
        )

        result = self.build_agent(provider).run(
            self.repository,
            AgentTask("Fix add().", ("add(2, 3) returns 5",)),
            (self.command,),
            run_id="duplicate-verification-agent",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.verifications, 2)
        rejected_step = json.loads(
            (
                self.agent_artifacts
                / "duplicate-verification-agent"
                / "steps"
                / "0002.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(rejected_step["event"]["kind"], "verification_rejected")
        self.assertIn("unchanged workspace", rejected_step["event"]["error"])

    def test_protocol_errors_are_bounded_and_receipted(self) -> None:
        provider = FakeProvider(["not json", "still not json"])
        limits = AgentLimits(max_steps=4, max_protocol_errors=1)

        result = self.build_agent(provider, limits=limits).run(
            self.repository,
            AgentTask("Fix add().", ("tests pass",)),
            (self.command,),
            run_id="protocol-agent",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.reason, "model protocol error budget exhausted")
        self.assertEqual(
            len(list((self.agent_artifacts / "protocol-agent" / "steps").glob("*.json"))),
            2,
        )

    def test_repeated_identical_actions_hit_stagnation_limit(self) -> None:
        repeated = decision("list_files", {})
        provider = FakeProvider([repeated, repeated, repeated])
        cadence = CadencePolicy(stagnation_limit=3)

        result = self.build_agent(provider, cadence=cadence).run(
            self.repository,
            AgentTask("Fix add().", ("tests pass",)),
            (self.command,),
            run_id="stagnant-agent",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.reason, "stagnation threshold reached")

    def test_workspace_oscillation_is_warned_and_bounded(self) -> None:
        original = "def add(left, right):\n    return left - right\n"
        alternate = "def add(left, right):\n    return left * right\n"
        provider = FakeProvider(
            [
                decision("read_file", {"path": "calc.py"}),
                decision(
                    "replace_text",
                    {
                        "path": "calc.py",
                        "old": "return left - right",
                        "new": "return left * right",
                        "expected_sha256": sha256(original),
                    },
                ),
                decision(
                    "replace_text",
                    {
                        "path": "calc.py",
                        "old": "return left * right",
                        "new": "return left - right",
                        "expected_sha256": sha256(alternate),
                    },
                ),
                decision(
                    "replace_text",
                    {
                        "path": "calc.py",
                        "old": "return left - right",
                        "new": "return left * right",
                        "expected_sha256": sha256(original),
                    },
                ),
            ]
        )

        result = self.build_agent(
            provider,
            cadence=CadencePolicy(
                stagnation_limit=2,
                verification_interval_mutations=32,
            ),
        ).run(
            self.repository,
            AgentTask("Fix add().", ("tests pass",)),
            (self.command,),
            run_id="workspace-cycle",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.reason, "workspace state cycle threshold reached")
        final_step = json.loads(
            (self.agent_artifacts / "workspace-cycle" / "steps" / "0004.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(final_step["event"]["workspace_cycle"]["repeat_count"], 2)

    def test_single_revert_can_break_cycle_with_a_new_repair(self) -> None:
        original = "def add(left, right):\n    return left - right\n"
        alternate = "def add(left, right):\n    return left * right\n"
        provider = FakeProvider(
            [
                decision("read_file", {"path": "calc.py"}),
                decision(
                    "replace_text",
                    {
                        "path": "calc.py",
                        "old": "return left - right",
                        "new": "return left * right",
                        "expected_sha256": sha256(original),
                    },
                ),
                decision(
                    "replace_text",
                    {
                        "path": "calc.py",
                        "old": "return left * right",
                        "new": "return left - right",
                        "expected_sha256": sha256(alternate),
                    },
                ),
                decision(
                    "replace_text",
                    {
                        "path": "calc.py",
                        "old": "return left - right",
                        "new": "return left + right",
                        "expected_sha256": sha256(original),
                    },
                ),
                finish("cycle broken"),
            ]
        )

        result = self.build_agent(
            provider,
            cadence=CadencePolicy(
                stagnation_limit=2,
                verification_interval_mutations=32,
            ),
        ).run(
            self.repository,
            AgentTask("Fix add().", ("add returns the sum",)),
            (self.command,),
            run_id="workspace-cycle-repair",
        )

        self.assertTrue(result.success)
        repair_context = json.loads(provider.messages[3][1].content)
        cycle_event = next(
            event
            for event in repair_context["recent_events"]
            if "workspace_cycle" in event
        )
        self.assertIn("criterion-specific", cycle_event["workspace_cycle"]["feedback"])

    def test_execution_error_feedback_is_sanitized(self) -> None:
        execution_command = CommandSpec(
            name="runtime",
            argv=(sys.executable, "-c", "print(missing_hidden_name)"),
            timeout_seconds=10,
            criteria=("module executes",),
        )
        provider = FakeProvider([finish("first"), decision("list_files", {})])

        self.build_agent(provider, limits=AgentLimits(max_steps=2)).run(
            self.repository,
            AgentTask("Repair runtime behavior.", ("module executes",)),
            (execution_command,),
            run_id="sanitized-runtime-feedback",
        )

        context = json.loads(provider.messages[1][1].content)
        failed = next(
            event
            for event in context["recent_events"]
            if event.get("kind") == "verification"
        )
        command_event = failed["commands"][0]
        self.assertEqual(command_event["failure_category"], "execution_error")
        self.assertIn("syntax, imports", command_event["feedback"])
        self.assertNotIn("missing_hidden_name", json.dumps(failed))

    def test_stagnation_ignores_narrative_reason_variation(self) -> None:
        responses = [
            json.dumps(
                {
                    "type": "tool",
                    "tool": "list_files",
                    "arguments": {},
                    "reason": reason,
                }
            )
            for reason in ("inspect", "inspect again", "one more inspection")
        ]
        result = self.build_agent(
            FakeProvider(responses),
            cadence=CadencePolicy(stagnation_limit=3),
        ).run(
            self.repository,
            AgentTask("Fix add().", ("tests pass",)),
            (self.command,),
            run_id="semantic-stagnation",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.reason, "stagnation threshold reached")
        self.assertEqual(result.steps, 3)

    def test_task_and_verification_inputs_are_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "instruction"):
            AgentTask("", ("tests pass",))
        with self.assertRaisesRegex(ValueError, "acceptance criteria"):
            AgentTask("Fix add().", ())
        with self.assertRaisesRegex(ValueError, "must be unique"):
            AgentTask("Fix add().", ("tests pass", " tests pass "))
        with self.assertRaisesRegex(ValueError, "verification commands"):
            self.build_agent(FakeProvider([])).run(
                self.repository,
                AgentTask("Fix add().", ("tests pass",)),
                (),
                run_id="no-verification",
            )

        uncovered = CommandSpec(
            name="uncovered",
            argv=(sys.executable, "-c", "pass"),
            timeout_seconds=10,
            criteria=("another criterion",),
        )
        with self.assertRaisesRegex(ValueError, "do not cover"):
            self.build_agent(FakeProvider([])).run(
                self.repository,
                AgentTask("Fix add().", ("tests pass",)),
                (uncovered,),
                run_id="uncovered-criterion",
            )

    def test_provider_failure_is_receipted(self) -> None:
        result = self.build_agent(ErrorProvider()).run(
            self.repository,
            AgentTask("Fix add().", ("tests pass",)),
            (self.command,),
            run_id="provider-error",
        )

        self.assertFalse(result.success)
        self.assertIn("provider failure", result.reason)
        step = json.loads(
            (
                self.agent_artifacts
                / "provider-error"
                / "steps"
                / "0001.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(step["event"]["kind"], "provider_error")

    def test_runtime_and_step_budgets_fail_closed(self) -> None:
        runtime_agent = self.build_agent(
            FakeProvider([]),
            limits=AgentLimits(max_steps=2, max_runtime_seconds=1),
        )
        with patch(
            "sisyphus_harness.agent.time.monotonic",
            side_effect=(0.0, 2.0),
        ):
            runtime = runtime_agent.run(
                self.repository,
                AgentTask("Fix add().", ("tests pass",)),
                (self.command,),
                run_id="runtime-budget",
            )
        self.assertEqual(runtime.reason, "runtime budget exhausted")
        self.assertEqual(runtime.steps, 0)

        exhausted = self.build_agent(
            FakeProvider([decision("list_files", {})]),
            limits=AgentLimits(max_steps=1),
        ).run(
            self.repository,
            AgentTask("Fix add().", ("tests pass",)),
            (self.command,),
            run_id="step-budget",
        )
        self.assertFalse(exhausted.success)
        self.assertEqual(exhausted.reason, "step budget exhausted")

    def test_intermediate_verification_runs_at_mutation_cadence(self) -> None:
        original = "def add(left, right):\n    return left - right\n"
        provider = FakeProvider(
            [
                decision("read_file", {"path": "calc.py"}),
                decision(
                    "replace_text",
                    {
                        "path": "calc.py",
                        "old": "return left - right",
                        "new": "return left + right",
                        "expected_sha256": sha256(original),
                    },
                ),
                finish(),
            ]
        )
        cadence = CadencePolicy(verification_interval_mutations=1)

        result = self.build_agent(provider, cadence=cadence).run(
            self.repository,
            AgentTask("Fix add().", ("tests pass",)),
            (self.command,),
            run_id="intermediate-verification",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.verifications, 2)
        mutation_step = json.loads(
            (
                self.agent_artifacts
                / "intermediate-verification"
                / "steps"
                / "0002.json"
            ).read_text(encoding="utf-8")
        )
        self.assertFalse(mutation_step["event"]["followup_verification"]["final"])

    def test_verifier_mutation_invalidates_agent_finish(self) -> None:
        mutating_command = CommandSpec(
            name="mutating verifier",
            argv=(
                sys.executable,
                "-c",
                "from pathlib import Path; Path('tracked.txt').write_text('changed\\n')",
            ),
            timeout_seconds=10,
            criteria=("workspace remains stable",),
        )
        result = self.build_agent(FakeProvider([finish()])).run(
            self.repository,
            AgentTask("Inspect repository.", ("workspace remains stable",)),
            (mutating_command,),
            run_id="mutating-verifier",
        )

        self.assertFalse(result.success)
        self.assertEqual(
            result.reason,
            "verification command mutated the workspace",
        )


if __name__ == "__main__":
    unittest.main()
