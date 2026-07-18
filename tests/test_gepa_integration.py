from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

from sisyphus_harness.config import CadencePolicy
from sisyphus_harness.evolution import (
    CandidatePolicy,
    EvaluationObservation,
    GepaEvolutionEngine,
)
from sisyphus_harness.provider import ChatResponse


GEPA_AVAILABLE = importlib.util.find_spec("gepa") is not None


class _ReflectionProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        prompt = "\n".join(message.content for message in messages)
        start = prompt.index("```") + 3
        end = prompt.index("```", start)
        current = prompt[start:end].strip()
        if current.startswith("{"):
            proposal = current
        else:
            proposal = (
                "Inspect hashes before editing and verify every acceptance criterion."
            )
        return ChatResponse(content=f"```\n{proposal}\n```")


def _evaluator(policy, example):
    improved = "hashes" in policy.strategy_prompt.lower()
    return EvaluationObservation(
        score=0.9 if improved else 0.2,
        success=improved,
        hard_gate_passed=True,
        diagnostics={
            "case_id": example["id"],
            "feedback": "inspect stale-write hashes" if not improved else "passed",
        },
        scores={"correctness": float(improved)},
    )


@unittest.skipUnless(GEPA_AVAILABLE, "GEPA evolution extra is not installed")
class GepaPackageIntegrationTests(unittest.TestCase):
    def test_installed_optimize_anything_api_evolves_candidate(self) -> None:
        provider = _ReflectionProvider()
        seed = CandidatePolicy(
            strategy_prompt="Make a focused change.",
            cadence=CadencePolicy(),
        )
        with tempfile.TemporaryDirectory() as directory:
            result = GepaEvolutionEngine(
                reflection_provider=provider,
                max_metric_calls=8,
                max_candidate_proposals=1,
                seed=0,
            ).optimize(
                seed=seed,
                evaluator=_evaluator,
                trainset=[{"id": "train-1"}, {"id": "train-2"}],
                valset=[{"id": "holdout-1"}],
                objective="Improve bounded coding-agent reliability.",
                background="Only strategy prompt and cadence may evolve.",
                run_dir=Path(directory) / "gepa",
            )

        self.assertIn("hashes", result.candidate.strategy_prompt.lower())
        self.assertEqual(result.metadata["engine"], "gepa.optimize_anything")
        self.assertEqual(result.metadata["val_aggregate_scores"], [0.2, 0.9])
        self.assertEqual(provider.calls, 1)


if __name__ == "__main__":
    unittest.main()
