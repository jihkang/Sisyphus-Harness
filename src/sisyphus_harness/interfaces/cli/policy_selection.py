from __future__ import annotations

from pathlib import Path

from ...authority import evolution_artifact_root, policy_root
from ...config import HarnessConfig
from ...contracts.policy import CandidatePolicy
from ...evolution import validate_evolution_id
from ...policy import PolicyRegistry
from ...workspace import contained_path


def resolve_policy(
    repo_root: Path,
    config: HarnessConfig,
    source: str,
) -> CandidatePolicy:
    if source == "config":
        return CandidatePolicy(
            strategy_prompt=config.strategy_prompt,
            cadence=config.cadence,
        )
    active = PolicyRegistry(policy_root(repo_root)).load_active()
    if active is None:
        raise ValueError("no active evolved policy is available")
    return active


def evolution_result_path(repo_root: Path, evolution_id: str) -> Path:
    validated_id = validate_evolution_id(evolution_id)
    return contained_path(
        evolution_artifact_root(repo_root),
        Path(validated_id) / "result.json",
        require_relative=True,
    )
