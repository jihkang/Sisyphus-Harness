from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from .codec import WireModel, strict_object
from .errors import CandidateError


@dataclass(frozen=True, slots=True)
class CadencePolicy(WireModel):
    compaction_interval_steps: int = 6
    context_char_limit: int = 48_000
    keep_recent_events: int = 4
    reflection_interval_steps: int = 4
    observation_interval_steps: int = 3
    verification_interval_mutations: int = 3
    stagnation_limit: int = 4

    def __post_init__(self) -> None:
        values = self.to_dict()
        if any(value <= 0 for value in values.values()):
            raise ValueError("cadence values must be positive")
        if not 1 <= self.compaction_interval_steps <= 64:
            raise ValueError("compaction_interval_steps is outside the supported range")
        if not 4000 <= self.context_char_limit <= 1_000_000:
            raise ValueError("context_char_limit is outside the supported range")
        if not 1 <= self.keep_recent_events <= 32:
            raise ValueError("keep_recent_events is outside the supported range")
        if not 1 <= self.reflection_interval_steps <= 64:
            raise ValueError("reflection_interval_steps is outside the supported range")
        if not 1 <= self.observation_interval_steps <= 64:
            raise ValueError("observation_interval_steps is outside the supported range")
        if not 1 <= self.verification_interval_mutations <= 32:
            raise ValueError(
                "verification_interval_mutations is outside the supported range"
            )
        if not 2 <= self.stagnation_limit <= 32:
            raise ValueError("stagnation_limit is outside the supported range")


@dataclass(frozen=True, slots=True)
class CandidatePolicy(WireModel):
    strategy_prompt: str
    cadence: CadencePolicy
    schema_version: str = "sisyphus_harness.policy_candidate.v1"

    def __post_init__(self) -> None:
        strategy = self.strategy_prompt.strip()
        if not strategy:
            raise CandidateError("strategy prompt must be non-empty")
        if len(strategy) > 8000:
            raise CandidateError("strategy prompt exceeds 8000 characters")
        if "```" in strategy:
            raise CandidateError("strategy prompt must not contain code fences")
        try:
            structured = json.loads(strategy)
        except json.JSONDecodeError:
            structured = None
        if isinstance(structured, (dict, list)):
            raise CandidateError("strategy prompt must be plain guidance, not metadata")

    def to_gepa_candidate(self) -> dict[str, str]:
        return {
            "strategy_prompt": self.strategy_prompt,
            "cadence_policy": json.dumps(
                self.cadence.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
            ),
        }

    def to_dict(self) -> dict[str, object]:
        payload = super().to_dict()
        payload["candidate_hash"] = self.candidate_hash
        return payload

    @property
    def candidate_hash(self) -> str:
        canonical = json.dumps(
            {
                "schema_version": self.schema_version,
                "strategy_prompt": self.strategy_prompt,
                "cadence": self.cadence.to_dict(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"

    @classmethod
    def from_gepa_candidate(cls, raw: object) -> CandidatePolicy:
        raw = strict_object(
            raw,
            required={"strategy_prompt", "cadence_policy"},
            label="GEPA candidate",
            error_type=CandidateError,
        )
        strategy = raw.get("strategy_prompt")
        cadence_raw = raw.get("cadence_policy")
        if not isinstance(strategy, str):
            raise CandidateError("candidate strategy_prompt must be a string")
        if not isinstance(cadence_raw, str):
            raise CandidateError("candidate cadence_policy must be a JSON string")
        try:
            cadence_payload = json.loads(cadence_raw)
        except json.JSONDecodeError as exc:
            raise CandidateError("candidate cadence_policy is invalid JSON") from exc
        return cls(
            strategy_prompt=strategy.strip(),
            cadence=_parse_candidate_cadence(cadence_payload),
        )

    @classmethod
    def from_dict(cls, raw: object) -> CandidatePolicy:
        raw = strict_object(
            raw,
            required={"schema_version", "strategy_prompt", "cadence"},
            optional={"candidate_hash"},
            label="candidate artifact",
            error_type=CandidateError,
        )
        if raw.get("schema_version") != "sisyphus_harness.policy_candidate.v1":
            raise CandidateError("unsupported candidate schema version")
        strategy = raw.get("strategy_prompt")
        if not isinstance(strategy, str):
            raise CandidateError("candidate strategy_prompt must be a string")
        candidate = cls(
            strategy_prompt=strategy,
            cadence=_parse_candidate_cadence(raw.get("cadence")),
        )
        recorded_hash = raw.get("candidate_hash")
        if recorded_hash is not None and recorded_hash != candidate.candidate_hash:
            raise CandidateError("candidate hash does not match artifact content")
        return candidate


def _parse_candidate_cadence(raw: object) -> CadencePolicy:
    allowed = {
        "compaction_interval_steps",
        "context_char_limit",
        "keep_recent_events",
        "reflection_interval_steps",
        "observation_interval_steps",
        "verification_interval_mutations",
        "stagnation_limit",
    }
    raw = strict_object(
        raw,
        required=allowed,
        label="candidate cadence",
        error_type=CandidateError,
    )
    values: dict[str, int] = {}
    for key in sorted(allowed):
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise CandidateError(f"candidate cadence {key} must be an integer")
        values[key] = value
    try:
        return CadencePolicy(**values)
    except ValueError as exc:
        raise CandidateError(str(exc)) from exc
