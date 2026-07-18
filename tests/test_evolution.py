from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import sys

from sisyphus_harness.config import CadencePolicy
from sisyphus_harness.evolution import (
    CandidateError,
    CandidatePolicy,
    EvaluationObservation,
    EvolutionEngineResult,
    EvolutionError,
    EvolutionRunner,
    GepaEvolutionEngine,
    _normalize_reflection_output,
    evaluate_policy,
)
from sisyphus_harness.provider import ChatResponse


class FakeEngine:
    def __init__(self, candidate: CandidatePolicy) -> None:
        self.candidate = candidate
        self.calls = 0

    def optimize(self, **kwargs) -> EvolutionEngineResult:
        self.calls += 1
        self.kwargs = kwargs
        return EvolutionEngineResult(
            candidate=self.candidate,
            metadata={"engine": "fake", "calls": self.calls},
        )


def evaluator(
    policy: CandidatePolicy,
    example: dict[str, object],
) -> EvaluationObservation:
    improved = "inspect hashes" in policy.strategy_prompt.lower()
    hard_gate = policy.cadence.verification_interval_mutations <= 4
    score = 0.9 if improved else 0.4
    if example.get("hard") and not improved:
        score = 0.2
    return EvaluationObservation(
        score=score,
        success=improved,
        hard_gate_passed=hard_gate,
        diagnostics={
            "case": example["id"],
            "feedback": "use stale-write hashes" if not improved else "passed",
        },
        scores={
            "correctness": float(improved),
            "efficiency": 0.8 if improved else 0.4,
        },
    )


class CandidatePolicyTests(unittest.TestCase):
    def test_candidate_round_trip_and_hash_validation(self) -> None:
        candidate = CandidatePolicy(
            strategy_prompt="Inspect hashes before editing.",
            cadence=CadencePolicy(),
        )

        parsed = CandidatePolicy.from_gepa_candidate(candidate.to_gepa_candidate())
        artifact = CandidatePolicy.from_dict(candidate.to_dict())

        self.assertEqual(parsed, candidate)
        self.assertEqual(artifact, candidate)
        tampered = candidate.to_dict()
        tampered["strategy_prompt"] = "tampered"
        with self.assertRaisesRegex(CandidateError, "hash does not match"):
            CandidatePolicy.from_dict(tampered)

    def test_candidate_rejects_unknown_missing_and_out_of_range_cadence(self) -> None:
        raw = CandidatePolicy(
            strategy_prompt="Inspect.",
            cadence=CadencePolicy(),
        ).to_gepa_candidate()
        raw["unknown"] = "value"
        with self.assertRaisesRegex(CandidateError, "unknown fields"):
            CandidatePolicy.from_gepa_candidate(raw)

        cadence = json.loads(raw["cadence_policy"])
        cadence.pop("stagnation_limit")
        with self.assertRaisesRegex(CandidateError, "missing fields"):
            CandidatePolicy.from_gepa_candidate(
                {
                    "strategy_prompt": "Inspect.",
                    "cadence_policy": json.dumps(cadence),
                }
            )

        cadence["stagnation_limit"] = 1
        with self.assertRaisesRegex(CandidateError, "stagnation_limit"):
            CandidatePolicy.from_gepa_candidate(
                {
                    "strategy_prompt": "Inspect.",
                    "cadence_policy": json.dumps(cadence),
                }
            )

    def test_observation_rejects_invalid_scores(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            EvaluationObservation(
                score=1.1,
                success=True,
                hard_gate_passed=True,
                diagnostics={},
                scores={},
            )
        with self.assertRaisesRegex(ValueError, "sub-scores"):
            EvaluationObservation(
                score=0.5,
                success=False,
                hard_gate_passed=False,
                diagnostics={},
                scores={"invalid": float("nan")},
            )

    def test_candidate_rejects_invalid_shapes_types_and_json(self) -> None:
        cadence = CadencePolicy()
        valid = CandidatePolicy("Inspect.", cadence)
        invalid_candidates = (
            (lambda: CandidatePolicy("", cadence), "non-empty"),
            (lambda: CandidatePolicy("x" * 8001, cadence), "exceeds"),
            (lambda: CandidatePolicy.from_gepa_candidate([]), "must be an object"),
            (
                lambda: CandidatePolicy.from_gepa_candidate(
                    {"strategy_prompt": 1, "cadence_policy": "{}"}
                ),
                "strategy_prompt must be a string",
            ),
            (
                lambda: CandidatePolicy.from_gepa_candidate(
                    {"strategy_prompt": "Inspect.", "cadence_policy": {}}
                ),
                "cadence_policy must be a JSON string",
            ),
            (
                lambda: CandidatePolicy.from_gepa_candidate(
                    {"strategy_prompt": "Inspect.", "cadence_policy": "{"}
                ),
                "invalid JSON",
            ),
            (
                lambda: CandidatePolicy.from_gepa_candidate(
                    {"strategy_prompt": "Inspect.", "cadence_policy": "[]"}
                ),
                "cadence must be an object",
            ),
            (
                lambda: CandidatePolicy.from_dict([]),
                "artifact must be an object",
            ),
            (
                lambda: CandidatePolicy.from_dict(
                    {**valid.to_dict(), "unknown": True}
                ),
                "artifact contains unknown fields",
            ),
            (
                lambda: CandidatePolicy.from_dict(
                    {**valid.to_dict(), "schema_version": "v2"}
                ),
                "unsupported candidate schema",
            ),
            (
                lambda: CandidatePolicy.from_dict(
                    {**valid.to_dict(), "strategy_prompt": 1}
                ),
                "strategy_prompt must be a string",
            ),
        )
        for construct, message in invalid_candidates:
            with self.subTest(message=message):
                with self.assertRaisesRegex(CandidateError, message):
                    construct()

        raw = json.loads(valid.to_gepa_candidate()["cadence_policy"])
        raw["stagnation_limit"] = True
        with self.assertRaisesRegex(CandidateError, "must be an integer"):
            CandidatePolicy.from_gepa_candidate(
                {
                    "strategy_prompt": "Inspect.",
                    "cadence_policy": json.dumps(raw),
                }
            )


class EvolutionRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.artifacts = Path(self.temporary_directory.name) / "evolution"
        self.seed = CandidatePolicy(
            strategy_prompt="Make a change.",
            cadence=CadencePolicy(),
        )
        self.improved = CandidatePolicy(
            strategy_prompt="Inspect hashes before editing.",
            cadence=CadencePolicy(
                compaction_interval_steps=4,
                context_char_limit=32000,
                keep_recent_events=4,
                reflection_interval_steps=3,
                observation_interval_steps=2,
                verification_interval_mutations=2,
                stagnation_limit=4,
            ),
        )
        self.trainset = [{"id": "train-1"}, {"id": "train-2"}]
        self.holdout = [{"id": "holdout-1", "hard": True}]

    def test_accepts_improvement_and_persists_independent_evidence(self) -> None:
        engine = FakeEngine(self.improved)
        runner = EvolutionRunner(
            engine=engine,
            artifact_root=self.artifacts,
            min_train_delta=0.1,
            min_holdout_delta=0.1,
        )

        result = runner.run(
            evolution_id="evolution-1",
            seed=self.seed,
            evaluator=evaluator,
            trainset=self.trainset,
            holdout=self.holdout,
            objective="Improve bounded coding agent reliability.",
            background="Safety authority is immutable.",
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.status, "proposed")
        self.assertGreater(
            result.candidate_holdout.mean_score,
            result.baseline_holdout.mean_score,
        )
        self.assertEqual(engine.calls, 1)
        self.assertIs(engine.kwargs["valset"], self.trainset)
        self.assertNotIn(self.holdout[0], engine.kwargs["valset"])
        run_dir = self.artifacts / "evolution-1"
        self.assertTrue((run_dir / "baseline.json").is_file())
        self.assertTrue((run_dir / "candidate.json").is_file())
        persisted = json.loads(
            (run_dir / "result.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            persisted["candidate"]["candidate_hash"],
            self.improved.candidate_hash,
        )

    def test_rejects_no_improvement_and_hard_gate_failure(self) -> None:
        no_improvement = EvolutionRunner(
            engine=FakeEngine(self.seed),
            artifact_root=self.artifacts,
            min_holdout_delta=0.1,
        ).run(
            evolution_id="no-improvement",
            seed=self.seed,
            evaluator=evaluator,
            trainset=self.trainset,
            holdout=self.holdout,
            objective="Improve.",
            background="Constraints.",
        )
        unsafe_cadence = CandidatePolicy(
            strategy_prompt="Inspect hashes before editing.",
            cadence=CadencePolicy(verification_interval_mutations=5),
        )
        hard_gate_failure = EvolutionRunner(
            engine=FakeEngine(unsafe_cadence),
            artifact_root=self.artifacts,
        ).run(
            evolution_id="hard-gate",
            seed=self.seed,
            evaluator=evaluator,
            trainset=self.trainset,
            holdout=self.holdout,
            objective="Improve.",
            background="Constraints.",
        )

        self.assertFalse(no_improvement.accepted)
        self.assertIn("holdout score delta", " ".join(no_improvement.reasons))
        self.assertFalse(hard_gate_failure.accepted)
        self.assertIn("hard gate", " ".join(hard_gate_failure.reasons))

    def test_empty_dataset_and_unsafe_id_are_rejected(self) -> None:
        runner = EvolutionRunner(
            engine=FakeEngine(self.improved),
            artifact_root=self.artifacts,
        )
        with self.assertRaisesRegex(EvolutionError, "non-empty"):
            runner.run(
                evolution_id="empty",
                seed=self.seed,
                evaluator=evaluator,
                trainset=[],
                holdout=self.holdout,
                objective="Improve.",
                background="Constraints.",
            )

    def test_train_and_holdout_must_be_disjoint_and_have_unique_ids(self) -> None:
        runner = EvolutionRunner(
            engine=FakeEngine(self.improved),
            artifact_root=self.artifacts,
        )
        with self.assertRaisesRegex(EvolutionError, "overlap"):
            runner.run(
                evolution_id="overlap",
                seed=self.seed,
                evaluator=evaluator,
                trainset=self.trainset,
                holdout=[{"id": "train-2"}],
                objective="Improve.",
                background="Constraints.",
            )
        with self.assertRaisesRegex(EvolutionError, "duplicate"):
            runner.run(
                evolution_id="duplicate-train",
                seed=self.seed,
                evaluator=evaluator,
                trainset=[{"id": "train-1"}, {"id": "train-1"}],
                holdout=self.holdout,
                objective="Improve.",
                background="Constraints.",
            )
        with self.assertRaisesRegex(EvolutionError, "unsafe"):
            runner.run(
                evolution_id="../escape",
                seed=self.seed,
                evaluator=evaluator,
                trainset=self.trainset,
                holdout=self.holdout,
                objective="Improve.",
                background="Constraints.",
            )

    def test_gepa_engine_validates_budget_before_optional_import(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_metric_calls"):
            GepaEvolutionEngine(
                reflection_provider=object(),
                max_metric_calls=0,
            )
        with self.assertRaisesRegex(ValueError, "max_candidate_proposals"):
            GepaEvolutionEngine(
                reflection_provider=object(),
                max_metric_calls=1,
                max_candidate_proposals=0,
            )

    def test_reflection_adapter_extracts_only_the_selected_component(self) -> None:
        strategy_prompt = """## Current Component
```
Inspect before editing.
```"""
        cadence_prompt = """## Current Component
```
{"compaction_interval_steps":2,"context_char_limit":6000}
```"""
        wrapped = json.dumps(
            {
                "strategy_prompt": "List files, read the target, then edit.",
                "cadence_policy": {"unsupported": True},
            }
        )
        cadence = json.dumps(
            {
                "cadence_policy": {
                    "compaction_interval_steps": 4,
                    "context_char_limit": 12000,
                }
            }
        )

        self.assertEqual(
            _normalize_reflection_output(wrapped, strategy_prompt),
            "List files, read the target, then edit.",
        )
        self.assertEqual(
            json.loads(_normalize_reflection_output(cadence, cadence_prompt)),
            {
                "compaction_interval_steps": 4,
                "context_char_limit": 12000,
            },
        )

    def test_reflection_adapter_extracts_final_prompt_from_model_commentary(self) -> None:
        prompt = """## Current Component
```
Inspect before editing.
```"""
        response = """Inspect before editing.
```
The improved version should verify each criterion immediately.
```
Inspect the source, make one atomic change, and verify every criterion."""

        self.assertEqual(
            _normalize_reflection_output(response, prompt),
            "Inspect the source, make one atomic change, and verify every criterion.",
        )

    def test_candidate_rejects_structured_or_fenced_strategy(self) -> None:
        for strategy in (
            "```\nInspect before editing.\n```",
            '{"strategy_prompt":"Inspect before editing."}',
        ):
            with self.subTest(strategy=strategy):
                with self.assertRaises(CandidateError):
                    CandidatePolicy(
                        strategy_prompt=strategy,
                        cadence=CadencePolicy(),
                    )

    def test_gepa_engine_adapts_provider_evaluator_and_metadata(self) -> None:
        class ReflectionProvider:
            def __init__(self) -> None:
                self.calls = []

            def complete(self, messages):
                self.calls.append(messages)
                return ChatResponse(content="reflection")

        class Config:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

        provider = ReflectionProvider()
        calls: dict[str, object] = {}
        optimize_module = ModuleType("gepa.optimize_anything")

        def optimize_anything(**kwargs):
            calls.update(kwargs)
            reflection = kwargs["config"].kwargs["reflection"].kwargs["reflection_lm"]
            self.assertEqual(
                reflection(
                    [
                        {"role": "system", "content": "reflect"},
                        {"content": {"problem": "stale writes"}},
                    ]
                ),
                "reflection",
            )
            self.assertEqual(reflection("plain prompt"), "reflection")
            with self.assertRaisesRegex(EvolutionError, "message objects"):
                reflection([{"content": "valid"}, "invalid"])
            with self.assertRaisesRegex(EvolutionError, "string or message list"):
                reflection(1)
            invalid_score, invalid_info = kwargs["evaluator"](
                {"unknown": "candidate"},
                self.trainset[0],
            )
            self.assertEqual(invalid_score, 0.0)
            self.assertFalse(invalid_info["candidate_valid"])
            score, side_info = kwargs["evaluator"](
                self.improved.to_gepa_candidate(),
                self.trainset[0],
            )
            self.assertEqual(score, 0.9)
            self.assertTrue(side_info["candidate_valid"])
            self.assertEqual(side_info["scores"]["hard_gate"], 1.0)
            return SimpleNamespace(
                best_candidate=self.improved.to_gepa_candidate(),
                best_idx=2,
                val_aggregate_scores=(0.4, 0.9),
            )

        optimize_module.EngineConfig = Config
        optimize_module.GEPAConfig = Config
        optimize_module.ReflectionConfig = Config
        optimize_module.optimize_anything = optimize_anything
        gepa_module = ModuleType("gepa")
        gepa_module.__path__ = []

        with patch.dict(
            sys.modules,
            {
                "gepa": gepa_module,
                "gepa.optimize_anything": optimize_module,
            },
        ):
            result = GepaEvolutionEngine(
                reflection_provider=provider,
                max_metric_calls=8,
                max_candidate_proposals=3,
                seed=7,
            ).optimize(
                seed=self.seed,
                evaluator=evaluator,
                trainset=self.trainset,
                valset=self.holdout,
                objective="Improve reliability.",
                background="Safety is immutable.",
                run_dir=self.artifacts / "gepa-unit",
            )

        self.assertEqual(result.candidate, self.improved)
        self.assertEqual(result.metadata["best_idx"], 2)
        self.assertEqual(result.metadata["val_aggregate_scores"], [0.4, 0.9])
        self.assertEqual(provider.calls[0][0].content, "reflect")
        self.assertEqual(
            provider.calls[0][1].content,
            '{"problem": "stale writes"}',
        )
        self.assertEqual(provider.calls[1][0].content, "plain prompt")
        engine_config = calls["config"].kwargs["engine"].kwargs
        self.assertFalse(engine_config["parallel"])
        self.assertEqual(engine_config["max_metric_calls"], 8)

    def test_gepa_engine_reports_missing_optional_dependency(self) -> None:
        with patch.dict(
            sys.modules,
            {"gepa": None, "gepa.optimize_anything": None},
        ):
            with self.assertRaisesRegex(EvolutionError, "not installed"):
                GepaEvolutionEngine(
                    reflection_provider=object(),
                    max_metric_calls=1,
                ).optimize(
                    seed=self.seed,
                    evaluator=evaluator,
                    trainset=self.trainset,
                    valset=self.holdout,
                    objective="Improve.",
                    background="Constraints.",
                    run_dir=self.artifacts / "missing-gepa",
                )

    def test_runner_rejects_regression_negative_delta_and_duplicate_run(self) -> None:
        for invalid_delta in (-0.1, 0.0, float("nan"), float("inf"), 1.1):
            with self.subTest(invalid_delta=invalid_delta):
                with self.assertRaisesRegex(ValueError, "finite, greater than 0"):
                    EvolutionRunner(
                        engine=FakeEngine(self.improved),
                        artifact_root=self.artifacts,
                        min_train_delta=invalid_delta,
                    )
        with self.assertRaisesRegex(ValueError, "finite, greater than 0"):
            EvolutionRunner(
                engine=FakeEngine(self.improved),
                artifact_root=self.artifacts,
                min_holdout_delta=0.0,
            )

        regressed = CandidatePolicy(
            strategy_prompt="Regressed candidate.",
            cadence=CadencePolicy(),
        )

        def regression_evaluator(policy, example):
            baseline = policy == self.seed
            return EvaluationObservation(
                score=0.8 if baseline else 0.2,
                success=baseline,
                hard_gate_passed=True,
                diagnostics={"case": example["id"]},
                scores={"correctness": 1.0 if baseline else 0.0},
            )

        result = EvolutionRunner(
            engine=FakeEngine(regressed),
            artifact_root=self.artifacts,
            min_train_delta=0.1,
            min_holdout_delta=0.1,
            require_all_holdout_success=False,
        ).run(
            evolution_id="regression",
            seed=self.seed,
            evaluator=regression_evaluator,
            trainset=self.trainset,
            holdout=self.holdout,
            objective="Improve.",
            background="Constraints.",
        )
        self.assertIn("training score delta", " ".join(result.reasons))
        self.assertIn("regressed holdout success", " ".join(result.reasons))

        with self.assertRaisesRegex(EvolutionError, "already exists"):
            EvolutionRunner(
                engine=FakeEngine(self.improved),
                artifact_root=self.artifacts,
            ).run(
                evolution_id="regression",
                seed=self.seed,
                evaluator=evaluator,
                trainset=self.trainset,
                holdout=self.holdout,
                objective="Improve.",
                background="Constraints.",
            )

    def test_evaluate_policy_requires_examples(self) -> None:
        with self.assertRaisesRegex(EvolutionError, "non-empty"):
            evaluate_policy(self.seed, [], evaluator)


if __name__ == "__main__":
    unittest.main()
