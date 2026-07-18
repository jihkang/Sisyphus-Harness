from __future__ import annotations

from dataclasses import dataclass
import math

from .policy import CandidatePolicy


@dataclass(frozen=True, slots=True)
class EvaluationObservation:
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

    def to_dict(self) -> dict[str, object]:
        return {
            "score": self.score,
            "success": self.success,
            "hard_gate_passed": self.hard_gate_passed,
            "diagnostics": self.diagnostics,
            "scores": self.scores,
        }


@dataclass(frozen=True, slots=True)
class EvaluationAggregate:
    count: int
    mean_score: float
    success_rate: float
    all_hard_gates_passed: bool
    observations: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "mean_score": self.mean_score,
            "success_rate": self.success_rate,
            "all_hard_gates_passed": self.all_hard_gates_passed,
            "observations": list(self.observations),
        }


@dataclass(frozen=True, slots=True)
class EvolutionResult:
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

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "evolution_id": self.evolution_id,
            "accepted": self.accepted,
            "status": self.status,
            "reasons": list(self.reasons),
            "baseline_train": self.baseline_train.to_dict(),
            "baseline_holdout": self.baseline_holdout.to_dict(),
            "candidate_train": self.candidate_train.to_dict(),
            "candidate_holdout": self.candidate_holdout.to_dict(),
            "candidate": self.candidate.to_dict(),
            "engine_metadata": self.engine_metadata,
            "artifact_path": self.artifact_path,
        }
