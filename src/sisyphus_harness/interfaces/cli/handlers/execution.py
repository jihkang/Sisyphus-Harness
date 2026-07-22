from __future__ import annotations

import argparse
from pathlib import Path
import uuid

from ....adapters.in_process import InProcessVerificationAdapter
from ....authority import evolution_artifact_root, verification_artifact_root
from ....benchmarks import CodingAgentBenchmarkEvaluator, load_benchmark_dataset
from ....config import ConfigError, load_harness_config, load_verification_config
from ....contracts.agent import AgentTask
from ....evolution import (
    EvolutionRunner,
    GepaEvolutionEngine,
    evaluate_policy,
    validate_evolution_id,
)
from ....provider import OpenAICompatibleProvider
from ....runtime import build_agent_run, build_verification_adapter
from ..io import repo_path
from ..policy_selection import resolve_policy
from ..result import CliResult


def handle_execution(args: argparse.Namespace, repo_root: Path) -> CliResult:
    if args.command == "verify":
        return _verify(args, repo_root)
    if args.command == "agent-run":
        return _agent_run(args, repo_root)
    if args.command == "benchmark-run":
        return _benchmark_run(args, repo_root)
    if args.command == "evolve":
        return _evolve(args, repo_root)
    raise AssertionError(f"unhandled execution command: {args.command}")


def _verify(args: argparse.Namespace, repo_root: Path) -> CliResult:
    config_path = repo_path(repo_root, args.config)
    try:
        config = load_harness_config(config_path)
        verification = config.verification
        verifier = build_verification_adapter(repo_root, config)
    except ConfigError as harness_error:
        try:
            verification = load_verification_config(config_path)
        except ConfigError:
            raise harness_error
        if not args.trusted_in_process:
            raise ConfigError(
                "verification-only config requires --trusted-in-process; use a "
                "harness config for contained verification"
            )
        verifier = InProcessVerificationAdapter.from_artifact_root(
            verification_artifact_root(repo_root)
        )
    receipt = verifier.verify(repo_root, verification.selected_commands)
    return CliResult(receipt.to_dict(), exit_code=0 if receipt.passed else 1)


def _agent_run(args: argparse.Namespace, repo_root: Path) -> CliResult:
    config_path = repo_path(repo_root, args.config)
    config = load_harness_config(config_path)
    policy = resolve_policy(repo_root, config, args.policy)
    provider = OpenAICompatibleProvider(config.provider)
    result = build_agent_run(
        authority_root=repo_root,
        workspace=repo_root,
        config_path=config_path,
        config=config,
        provider=provider,
        policy=policy,
    ).run(
        repo_root,
        AgentTask(args.task, tuple(args.criterion)),
        config.verification.selected_commands,
        run_id=args.run_id,
    )
    return CliResult(result.to_dict(), exit_code=0 if result.success else 1)


def _benchmark_run(args: argparse.Namespace, repo_root: Path) -> CliResult:
    config = load_harness_config(repo_path(repo_root, args.config))
    policy = resolve_policy(repo_root, config, args.policy)
    examples = load_benchmark_dataset(repo_path(repo_root, args.dataset))
    evaluator = CodingAgentBenchmarkEvaluator(
        provider=OpenAICompatibleProvider(config.provider),
        limits=config.limits,
        rollout_root=evolution_artifact_root(repo_root) / "benchmark-rollouts",
    )
    aggregate = evaluate_policy(policy, examples, evaluator)
    return CliResult(
        {
            "policy": policy.to_dict(),
            "evaluation": aggregate.to_dict(),
        },
        exit_code=0 if aggregate.success_rate == 1.0 else 1,
    )


def _evolve(args: argparse.Namespace, repo_root: Path) -> CliResult:
    config = load_harness_config(repo_path(repo_root, args.config))
    seed = resolve_policy(repo_root, config, args.seed_policy)
    trainset = load_benchmark_dataset(repo_path(repo_root, args.train_dataset))
    holdout = load_benchmark_dataset(repo_path(repo_root, args.holdout_dataset))
    evolution_id = validate_evolution_id(
        args.evolution_id or f"evolution-{uuid.uuid4().hex}"
    )
    provider = OpenAICompatibleProvider(config.provider)
    reflection_provider = OpenAICompatibleProvider(
        config.provider,
        json_mode=False,
    )
    artifact_root = evolution_artifact_root(repo_root)
    evaluator = CodingAgentBenchmarkEvaluator(
        provider=provider,
        limits=config.limits,
        rollout_root=artifact_root / f"{evolution_id}-rollouts",
    )
    result = EvolutionRunner(
        engine=GepaEvolutionEngine(
            reflection_provider=reflection_provider,
            max_metric_calls=config.evolution.max_metric_calls,
            max_candidate_proposals=config.evolution.max_candidate_proposals,
            seed=config.evolution.seed,
        ),
        artifact_root=artifact_root,
        min_train_delta=config.evolution.min_train_delta,
        min_holdout_delta=config.evolution.min_holdout_delta,
    ).run(
        evolution_id=evolution_id,
        seed=seed,
        evaluator=evaluator,
        trainset=trainset,
        holdout=holdout,
        objective=(
            "Improve the bounded coding agent's hidden-test correctness and "
            "efficiency by evolving only its strategy prompt and cadence."
        ),
        background=(
            "The safety prompt, tool schemas, path boundaries, verifier, budgets, "
            "queue authority, and operator activation gate are immutable. Preserve "
            "strict JSON output and use trace_summary to correct repeated, failed, "
            "or premature actions. Prefer general strategies that turn every "
            "stated acceptance clause into positive and negative behavior checks, "
            "preserve explicit rejection boundaries, use known_file_hashes exactly, "
            "and repair the specific failed criterion before finishing again. A "
            "strategy_prompt mutation must be plain imperative guidance, not JSON "
            "or metadata. A cadence_policy mutation must remain exact schema-valid "
            "JSON."
        ),
    )
    return CliResult(result.to_dict(), exit_code=0 if result.accepted else 1)
