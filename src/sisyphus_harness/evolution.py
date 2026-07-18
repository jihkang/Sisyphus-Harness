from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Any, Callable, Protocol

from .contracts.errors import CandidateError
from .contracts.evolution import (
    EvaluationAggregate,
    EvaluationObservation,
    EvolutionResult,
)
from .contracts.policy import CandidatePolicy
from .provider import ChatMessage, ChatProvider
from .receipts import write_json_atomic
from .workspace import contained_path


class EvolutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EvolutionEngineResult:
    candidate: CandidatePolicy
    metadata: dict[str, object]


EvaluationFunction = Callable[
    [CandidatePolicy, dict[str, Any]],
    EvaluationObservation,
]


class EvolutionEngine(Protocol):
    def optimize(
        self,
        *,
        seed: CandidatePolicy,
        evaluator: EvaluationFunction,
        trainset: list[dict[str, Any]],
        valset: list[dict[str, Any]],
        objective: str,
        background: str,
        run_dir: Path,
    ) -> EvolutionEngineResult:
        ...


class GepaEvolutionEngine:
    def __init__(
        self,
        *,
        reflection_provider: ChatProvider,
        max_metric_calls: int,
        max_candidate_proposals: int | None = None,
        seed: int = 0,
    ) -> None:
        if max_metric_calls <= 0:
            raise ValueError("GEPA max_metric_calls must be positive")
        if max_candidate_proposals is not None and max_candidate_proposals <= 0:
            raise ValueError("GEPA max_candidate_proposals must be positive")
        self.reflection_provider = reflection_provider
        self.max_metric_calls = max_metric_calls
        self.max_candidate_proposals = max_candidate_proposals
        self.seed = seed

    def optimize(
        self,
        *,
        seed: CandidatePolicy,
        evaluator: EvaluationFunction,
        trainset: list[dict[str, Any]],
        valset: list[dict[str, Any]],
        objective: str,
        background: str,
        run_dir: Path,
    ) -> EvolutionEngineResult:
        try:
            from gepa.optimize_anything import (
                EngineConfig,
                GEPAConfig,
                ReflectionConfig,
                optimize_anything,
            )
        except ImportError as exc:
            raise EvolutionError(
                "GEPA is not installed; install the 'evolution' extra"
            ) from exc

        def reflection_lm(prompt, **_: object) -> str:
            if isinstance(prompt, str):
                converted = (ChatMessage(role="user", content=prompt),)
            elif isinstance(prompt, list):
                converted = tuple(
                    ChatMessage(
                        role=str(message.get("role", "user")),
                        content=_message_content(message.get("content")),
                    )
                    for message in prompt
                    if isinstance(message, dict)
                )
                if len(converted) != len(prompt):
                    raise EvolutionError(
                        "GEPA reflection messages must be strings or message objects"
                    )
            else:
                raise EvolutionError(
                    "GEPA reflection prompt must be a string or message list"
                )
            response = self.reflection_provider.complete(converted).content
            prompt_text = "\n".join(message.content for message in converted)
            return _normalize_reflection_output(response, prompt_text)

        def gepa_evaluator(raw_candidate, example):
            try:
                policy = CandidatePolicy.from_gepa_candidate(raw_candidate)
            except CandidateError as exc:
                return 0.0, {
                    "candidate_valid": False,
                    "error": str(exc),
                    "scores": {
                        "success": 0.0,
                        "hard_gate": 0.0,
                    },
                }
            observation = evaluator(policy, example)
            side_info = dict(observation.diagnostics)
            side_info.update(
                {
                    "candidate_valid": True,
                    "success": observation.success,
                    "hard_gate_passed": observation.hard_gate_passed,
                    "scores": {
                        **observation.scores,
                        "success": float(observation.success),
                        "hard_gate": float(observation.hard_gate_passed),
                    },
                }
            )
            return observation.score, side_info

        run_dir.mkdir(parents=True, exist_ok=True)
        result = optimize_anything(
            seed_candidate=seed.to_gepa_candidate(),
            evaluator=gepa_evaluator,
            dataset=trainset,
            valset=valset,
            objective=objective,
            background=background,
            config=GEPAConfig(
                engine=EngineConfig(
                    run_dir=str(run_dir),
                    seed=self.seed,
                    max_metric_calls=self.max_metric_calls,
                    max_candidate_proposals=self.max_candidate_proposals,
                    parallel=False,
                    cache_evaluation=True,
                    raise_on_exception=False,
                ),
                reflection=ReflectionConfig(reflection_lm=reflection_lm),
            ),
        )
        candidate = CandidatePolicy.from_gepa_candidate(result.best_candidate)
        return EvolutionEngineResult(
            candidate=candidate,
            metadata={
                "engine": "gepa.optimize_anything",
                "best_idx": getattr(result, "best_idx", None),
                "val_aggregate_scores": list(
                    getattr(result, "val_aggregate_scores", [])
                ),
                "max_metric_calls": self.max_metric_calls,
                "max_candidate_proposals": self.max_candidate_proposals,
                "seed": self.seed,
            },
        )


class EvolutionRunner:
    def __init__(
        self,
        *,
        engine: EvolutionEngine,
        artifact_root: Path,
        min_train_delta: float = 0.01,
        min_holdout_delta: float = 0.01,
        require_all_holdout_success: bool = True,
    ) -> None:
        if any(
            not math.isfinite(value) or not 0 < value <= 1
            for value in (min_train_delta, min_holdout_delta)
        ):
            raise ValueError(
                "evolution score deltas must be finite, greater than 0, "
                "and at most 1"
            )
        self.engine = engine
        self.artifact_root = artifact_root
        self.min_train_delta = min_train_delta
        self.min_holdout_delta = min_holdout_delta
        self.require_all_holdout_success = require_all_holdout_success

    def run(
        self,
        *,
        evolution_id: str,
        seed: CandidatePolicy,
        evaluator: EvaluationFunction,
        trainset: list[dict[str, Any]],
        holdout: list[dict[str, Any]],
        objective: str,
        background: str,
    ) -> EvolutionResult:
        if not trainset or not holdout:
            raise EvolutionError("evolution requires non-empty train and holdout sets")
        _require_disjoint_datasets(trainset, holdout)
        run_dir = _new_run_dir(self.artifact_root, evolution_id)
        baseline_train = _evaluate_set(seed, trainset, evaluator)
        baseline_holdout = _evaluate_set(seed, holdout, evaluator)
        write_json_atomic(
            run_dir / "baseline.json",
            {
                "candidate": seed.to_dict(),
                "train": baseline_train.to_dict(),
                "holdout": baseline_holdout.to_dict(),
            },
        )
        engine_result = self.engine.optimize(
            seed=seed,
            evaluator=evaluator,
            trainset=trainset,
            valset=trainset,
            objective=objective,
            background=background,
            run_dir=run_dir / "gepa",
        )
        candidate_train = _evaluate_set(
            engine_result.candidate,
            trainset,
            evaluator,
        )
        candidate_holdout = _evaluate_set(
            engine_result.candidate,
            holdout,
            evaluator,
        )
        reasons: list[str] = []
        if (
            candidate_train.mean_score
            < baseline_train.mean_score + self.min_train_delta
        ):
            reasons.append("candidate did not meet the training score delta")
        if (
            candidate_holdout.mean_score
            < baseline_holdout.mean_score + self.min_holdout_delta
        ):
            reasons.append("candidate did not meet the holdout score delta")
        if candidate_holdout.success_rate < baseline_holdout.success_rate:
            reasons.append("candidate regressed holdout success rate")
        if (
            self.require_all_holdout_success
            and candidate_holdout.success_rate < 1.0
        ):
            reasons.append("candidate did not pass every holdout case")
        if not candidate_train.all_hard_gates_passed:
            reasons.append("candidate failed a training hard gate")
        if not candidate_holdout.all_hard_gates_passed:
            reasons.append("candidate failed a holdout hard gate")
        accepted = not reasons
        status = "proposed" if accepted else "rejected"
        result = EvolutionResult(
            evolution_id=evolution_id,
            accepted=accepted,
            status=status,
            reasons=tuple(reasons),
            baseline_train=baseline_train,
            baseline_holdout=baseline_holdout,
            candidate_train=candidate_train,
            candidate_holdout=candidate_holdout,
            candidate=engine_result.candidate,
            engine_metadata=engine_result.metadata,
            artifact_path=str(run_dir),
        )
        write_json_atomic(run_dir / "candidate.json", result.candidate.to_dict())
        write_json_atomic(run_dir / "result.json", result.to_dict())
        return result


def _normalize_reflection_output(content: str, prompt: str) -> str:
    stripped = content.strip()
    fenced_parts = [
        part.strip()
        for part in re.split(
            r"```(?:[A-Za-z0-9_-]+)?[ \t]*(?:\r?\n)?",
            stripped,
        )
        if part.strip()
    ]
    candidate = fenced_parts[-1] if "```" in stripped and fenced_parts else stripped
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        payload = None

    current_match = re.search(
        r"## Current Component.*?```\s*(.*?)\s*```",
        prompt,
        flags=re.DOTALL,
    )
    current = current_match.group(1) if current_match is not None else ""
    cadence_component = "compaction_interval_steps" in current
    if cadence_component:
        if isinstance(payload, dict):
            nested = payload.get("cadence_policy")
            if isinstance(nested, str):
                return nested.strip()
            if isinstance(nested, dict):
                return json.dumps(nested, sort_keys=True, separators=(",", ":"))
            return json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return candidate
    if isinstance(payload, dict):
        strategy = payload.get("strategy_prompt")
        if isinstance(strategy, str) and strategy.strip():
            return strategy.strip()
    return candidate

def _evaluate_set(
    policy: CandidatePolicy,
    examples: list[dict[str, Any]],
    evaluator: EvaluationFunction,
) -> EvaluationAggregate:
    observations: list[dict[str, object]] = []
    total_score = 0.0
    successes = 0
    all_gates = True
    for example in examples:
        observation = evaluator(policy, example)
        total_score += observation.score
        successes += int(observation.success)
        all_gates = all_gates and observation.hard_gate_passed
        observations.append(
            {
                "example_id": str(example.get("id", "unknown")),
                **observation.to_dict(),
            }
        )
    count = len(examples)
    return EvaluationAggregate(
        count=count,
        mean_score=total_score / count,
        success_rate=successes / count,
        all_hard_gates_passed=all_gates,
        observations=tuple(observations),
    )


def evaluate_policy(
    policy: CandidatePolicy,
    examples: list[dict[str, Any]],
    evaluator: EvaluationFunction,
) -> EvaluationAggregate:
    if not examples:
        raise EvolutionError("policy evaluation requires non-empty examples")
    return _evaluate_set(policy, examples, evaluator)


def _new_run_dir(root: Path, evolution_id: str) -> Path:
    validated_id = validate_evolution_id(evolution_id)
    root.mkdir(parents=True, exist_ok=True)
    run_dir = contained_path(root, validated_id, require_relative=True)
    if run_dir.exists():
        raise EvolutionError(f"evolution run already exists: {validated_id}")
    run_dir.mkdir(parents=True)
    return run_dir


def validate_evolution_id(evolution_id: str) -> str:
    if (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", evolution_id) is None
        or evolution_id in {".", ".."}
    ):
        raise EvolutionError("evolution ID contains unsafe characters")
    return evolution_id


def _require_disjoint_datasets(
    trainset: list[dict[str, Any]],
    holdout: list[dict[str, Any]],
) -> None:
    train_ids = _unique_example_ids(trainset, "training")
    holdout_ids = _unique_example_ids(holdout, "holdout")
    overlap = sorted(train_ids.intersection(holdout_ids))
    if overlap:
        raise EvolutionError(
            "training and holdout sets overlap by example ID: " + ", ".join(overlap)
        )


def _unique_example_ids(
    examples: list[dict[str, Any]],
    label: str,
) -> set[str]:
    identifiers: set[str] = set()
    for index, example in enumerate(examples):
        raw = example.get("id")
        if not isinstance(raw, str) or not raw.strip():
            raise EvolutionError(f"{label} example {index} requires a non-empty ID")
        identifier = raw.strip()
        if identifier in identifiers:
            raise EvolutionError(f"{label} set contains duplicate example ID: {identifier}")
        identifiers.add(identifier)
    return identifiers


def _message_content(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)
