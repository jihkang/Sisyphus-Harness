from __future__ import annotations

from collections import deque
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from sisyphus_harness.benchmarks import (
    CodingAgentBenchmarkEvaluator,
    load_benchmark_dataset,
)
from sisyphus_harness.config import AgentLimits, CadencePolicy
from sisyphus_harness.evolution import CandidatePolicy
from sisyphus_harness.provider import ChatResponse


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
            "reason": "benchmark test",
        }
    )


class BenchmarkTests(unittest.TestCase):
    @property
    def benchmark_root(self) -> Path:
        return Path(__file__).resolve().parents[1] / "benchmarks"

    def test_loads_repository_benchmark_datasets(self) -> None:
        train = load_benchmark_dataset(self.benchmark_root / "train.json")
        holdout = load_benchmark_dataset(self.benchmark_root / "holdout.json")
        holdout_v1 = load_benchmark_dataset(
            self.benchmark_root / "holdout-v1.json"
        )
        holdout_v2 = load_benchmark_dataset(
            self.benchmark_root / "holdout-v2.json"
        )

        self.assertEqual(
            [case["id"] for case in train],
            [
                "python-add",
                "python-clamp",
                "python-page-size",
                "python-label",
            ],
        )
        self.assertEqual(
            [case["id"] for case in holdout],
            ["python-retry-delay", "python-cache-key"],
        )
        self.assertEqual(
            [case["id"] for case in holdout_v1],
            ["python-slugify", "python-port"],
        )
        self.assertEqual(
            [case["id"] for case in holdout_v2],
            ["python-page-window", "python-completion-percent"],
        )
        self.assertTrue(Path(train[0]["workspace_source"]).is_dir())
        self.assertTrue(Path(train[0]["verifiers"][0]["script"]).is_file())
        self.assertEqual(
            [verifier["criterion"] for verifier in train[1]["verifiers"]],
            train[1]["acceptance_criteria"],
        )

    def test_fixture_baselines_fail_hidden_verifiers(self) -> None:
        examples = load_benchmark_dataset(self.benchmark_root / "train.json")
        examples += load_benchmark_dataset(self.benchmark_root / "holdout.json")

        for example in examples:
            results = [
                subprocess.run(
                    [sys.executable, verifier["script"]],
                    cwd=example["workspace_source"],
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                for verifier in example["verifiers"]
            ]
            self.assertTrue(
                any(result.returncode != 0 for result in results),
                msg=f"fixture unexpectedly passed: {example['id']}",
            )

    def test_each_hidden_verifier_can_import_from_its_workspace(self) -> None:
        examples = load_benchmark_dataset(self.benchmark_root / "train.json")
        examples += load_benchmark_dataset(self.benchmark_root / "holdout.json")

        for example in examples:
            for verifier in example["verifiers"]:
                completed = subprocess.run(
                    [sys.executable, verifier["script"]],
                    cwd=example["workspace_source"],
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertNotIn(
                    "ModuleNotFoundError",
                    completed.stderr,
                    msg=f"verifier cannot import workspace: {example['id']}",
                )

    def test_v3_holdout_verifiers_accept_spec_conforming_references(self) -> None:
        references = {
            "python-retry-delay": (
                "backoff.py",
                "def retry_delay(attempt, base_delay):\n"
                "    if (isinstance(attempt, bool) or not isinstance(attempt, int) "
                "or isinstance(base_delay, bool) or not isinstance(base_delay, int)):\n"
                "        raise ValueError('inputs must be integers')\n"
                "    if not 1 <= attempt <= 8 or not 1 <= base_delay <= 30:\n"
                "        raise ValueError('input outside supported range')\n"
                "    return min(60, base_delay * 2 ** (attempt - 1))\n",
            ),
            "python-cache-key": (
                "cache_keys.py",
                "import re\n\n"
                "def normalize_cache_key(value):\n"
                "    if not isinstance(value, str):\n"
                "        raise ValueError('value must be a string')\n"
                "    filtered = re.sub(r'[^a-z0-9\\s_-]', '', value.lower())\n"
                "    return re.sub(r'[\\s_-]+', '-', filtered).strip('-')\n",
            ),
        }
        examples = load_benchmark_dataset(self.benchmark_root / "holdout.json")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for example in examples:
                workspace = root / example["id"]
                shutil.copytree(example["workspace_source"], workspace)
                module_name, content = references[example["id"]]
                (workspace / module_name).write_text(content, encoding="utf-8")
                for verifier in example["verifiers"]:
                    completed = subprocess.run(
                        [sys.executable, verifier["script"]],
                        cwd=workspace,
                        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        msg=(
                            f"reference failed {example['id']}/"
                            f"{verifier['name']}: {completed.stderr}"
                        ),
                    )

    def test_v3_holdout_lock_matches_every_frozen_file(self) -> None:
        lock = json.loads(
            (self.benchmark_root / "holdout-v3.lock.json").read_text(
                encoding="utf-8"
            )
        )
        canonical_lines = []
        repository_root = self.benchmark_root.parent
        for entry in lock["files"]:
            content = (repository_root / entry["path"]).read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            self.assertEqual(digest, entry["sha256"], msg=entry["path"])
            canonical_lines.append(f"{digest}  {entry['path']}\n")

        aggregate = hashlib.sha256("".join(canonical_lines).encode("utf-8")).hexdigest()
        self.assertEqual(aggregate, lock["aggregate_sha256"])
        self.assertTrue(lock["frozen_before_model_evaluation"])

    def test_evaluator_runs_agent_in_isolated_copy_and_scores_success(self) -> None:
        example = load_benchmark_dataset(self.benchmark_root / "train.json")[0]
        original = "def add(left, right):\n    return left - right\n"
        expected_hash = (
            "sha256:" + hashlib.sha256(original.encode("utf-8")).hexdigest()
        )
        provider = FakeProvider(
            [
                action("list_files", {}),
                action("read_file", {"path": "math_utils.py"}),
                action(
                    "replace_text",
                    {
                        "path": "math_utils.py",
                        "old": "return left - right",
                        "new": "return left + right",
                        "expected_sha256": expected_hash,
                    },
                ),
                json.dumps({"type": "finish", "summary": "fixed addition"}),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            evaluator = CodingAgentBenchmarkEvaluator(
                provider=provider,
                limits=AgentLimits(max_steps=8, max_compactions=2),
                rollout_root=Path(directory),
            )
            policy = CandidatePolicy(
                strategy_prompt="Inspect hashes before editing.",
                cadence=CadencePolicy(
                    verification_interval_mutations=2,
                ),
            )

            observation = evaluator(policy, example)

            self.assertTrue(observation.success)
            self.assertTrue(observation.hard_gate_passed)
            self.assertGreater(observation.score, 0.85)
            trace = observation.diagnostics["trace_summary"]
            self.assertEqual(trace["total_steps"], 4)
            self.assertEqual(
                [action.get("tool") for action in trace["actions"]],
                ["list_files", "read_file", "replace_text", None],
            )
            self.assertTrue(trace["actions"][-1]["verification_passed"])
            self.assertEqual(
                Path(example["workspace_source"], "math_utils.py").read_text(
                    encoding="utf-8"
                ),
                original,
            )
            rollout = Path(observation.diagnostics["rollout_path"])
            self.assertFalse(any(rollout.rglob("__pycache__")))
            self.assertIn(
                "return left + right",
                (rollout / "workspace" / "math_utils.py").read_text(
                    encoding="utf-8"
                ),
            )

    def test_evaluator_initial_state_is_reproducible(self) -> None:
        example = load_benchmark_dataset(self.benchmark_root / "train.json")[0]
        responses = [json.dumps({"type": "finish", "summary": "inspect baseline"})]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = CandidatePolicy(
                strategy_prompt="Inspect before editing.",
                cadence=CadencePolicy(),
            )
            observations = [
                CodingAgentBenchmarkEvaluator(
                    provider=FakeProvider(list(responses)),
                    limits=AgentLimits(max_steps=1),
                    rollout_root=root,
                )(policy, example)
                for _ in range(2)
            ]

            initial_states = [
                observation.diagnostics["result"]["workspace_state_before"]
                for observation in observations
            ]
            commits = [
                subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=Path(observation.diagnostics["rollout_path"]) / "workspace",
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=True,
                ).stdout.strip()
                for observation in observations
            ]

            self.assertEqual(initial_states[0], initial_states[1])
            self.assertEqual(commits[0], commits[1])

    def test_evaluator_scores_partial_criterion_correctness(self) -> None:
        example = load_benchmark_dataset(self.benchmark_root / "train.json")[1]
        original = "def clamp(value, lower, upper):\n    return min(lower, max(upper, value))\n"
        updated = (
            "def clamp(value, lower, upper):\n"
            "    if lower > upper:\n"
            "        raise ValueError('inverted range')\n"
            "    return min(lower, max(upper, value))\n"
        )
        provider = FakeProvider(
            [
                action("read_file", {"path": "bounds.py"}),
                action(
                    "write_file",
                    {
                        "path": "bounds.py",
                        "content": updated,
                        "expected_sha256": "sha256:"
                        + hashlib.sha256(original.encode("utf-8")).hexdigest(),
                    },
                ),
                json.dumps({"type": "finish", "summary": "partial fix"}),
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            observation = CodingAgentBenchmarkEvaluator(
                provider=provider,
                limits=AgentLimits(max_steps=3, max_compactions=2),
                rollout_root=Path(directory),
            )(
                CandidatePolicy(
                    strategy_prompt="Inspect and repair one criterion at a time.",
                    cadence=CadencePolicy(),
                ),
                example,
            )

        self.assertFalse(observation.success)
        self.assertEqual(observation.scores["correctness"], 0.5)
        self.assertEqual(observation.diagnostics["criterion_pass_rate"], 0.5)
        self.assertGreater(observation.score, 0.35)

    def test_evaluator_converts_runtime_error_to_failed_hard_gate(self) -> None:
        example = {"id": "broken"}
        with tempfile.TemporaryDirectory() as directory:
            evaluator = CodingAgentBenchmarkEvaluator(
                provider=FakeProvider([]),
                limits=AgentLimits(max_steps=2),
                rollout_root=Path(directory),
            )
            policy = CandidatePolicy(
                strategy_prompt="Inspect.",
                cadence=CadencePolicy(),
            )

            observation = evaluator(policy, example)

            self.assertFalse(observation.success)
            self.assertFalse(observation.hard_gate_passed)
            self.assertEqual(observation.score, 0.0)
            self.assertIn("BenchmarkError", observation.diagnostics["error"])


if __name__ == "__main__":
    unittest.main()
