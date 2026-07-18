from __future__ import annotations

from dataclasses import dataclass
import math

from .codec import WireModel
from .policy import CandidatePolicy


@dataclass(frozen=True, slots=True)
class EvaluationObservation(WireModel):
    score: float
    success: bool
    hard_gate_passed: bool
    diagnostics: dict[str, object]
    scores: dict[str, float]

    def __post_init__(self) -> None:
        if not math.isfinite(self.score) or not 0 <= self.score <= 1:
            raise ValueError("evaluation score must be finite and between 0 and 1")
        if any(
            not math.isfinite(value) or not 0 <= value <= 1
            for value in self.scores.values()
        ):
            raise ValueError("evaluation sub-scores must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class EvaluationAggregate(WireModel):
    count: int
    mean_score: float
    success_rate: float
    all_hard_gates_passed: bool
    observations: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class EvolutionResult(WireModel):
    evolution_id: str
    accepted: bool
    status: str
    reasons: tuple[str, ...]
    baseline_train: EvaluationAggregate
    baseline_holdout: EvaluationAggregate
    candidate_train: EvaluationAggregate
    candidate_holdout: EvaluationAggregate
    candidate: CandidatePolicy
    engine_metadata: dict[str, object]
    artifact_path: str
    schema_version: str = "sisyphus_harness.evolution_result.v1"
