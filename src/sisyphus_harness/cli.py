from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence
import uuid

from .agent import LocalCodingAgent
from .authority import (
    agent_artifact_root,
    authority_database_path,
    evolution_artifact_root,
    policy_root,
    verification_artifact_root,
)
from .benchmarks import CodingAgentBenchmarkEvaluator, load_benchmark_dataset
from .config import (
    ConfigError,
    HarnessConfig,
    load_harness_config,
    load_verification_config,
)
from .contracts.agent import AgentTask
from .contracts.policy import CandidatePolicy
from .database import Database
from .evolution import (
    EvolutionRunner,
    GepaEvolutionEngine,
    evaluate_policy,
    validate_evolution_id,
)
from .policy import PolicyRegistry
from .provider import OpenAICompatibleProvider
from .queue import JobQueue
from .verifier import BoundedVerifier
from .worker import CodingWorker
from .workspace import contained_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sisyphus-harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    _repo_argument(init_parser)

    enqueue_parser = subparsers.add_parser("queue-enqueue")
    _repo_argument(enqueue_parser)
    enqueue_parser.add_argument("--kind", required=True)
    enqueue_parser.add_argument("--idempotency-key", required=True)
    enqueue_parser.add_argument("--payload-json", required=True)

    claim_parser = subparsers.add_parser("queue-claim")
    _repo_argument(claim_parser)
    claim_parser.add_argument("--worker-id", required=True)
    claim_parser.add_argument("--lease-seconds", type=float, default=60.0)

    heartbeat_parser = subparsers.add_parser("queue-heartbeat")
    _repo_argument(heartbeat_parser)
    heartbeat_parser.add_argument("--job-id", required=True)
    heartbeat_parser.add_argument("--worker-id", required=True)
    heartbeat_parser.add_argument("--lease-seconds", type=float, default=60.0)

    finish_parser = subparsers.add_parser("queue-finish")
    _repo_argument(finish_parser)
    finish_parser.add_argument("--job-id", required=True)
    finish_parser.add_argument("--worker-id", required=True)
    finish_parser.add_argument("--status", choices=("completed", "failed"), required=True)
    finish_parser.add_argument("--result-json", required=True)

    get_parser = subparsers.add_parser("queue-get")
    _repo_argument(get_parser)
    get_parser.add_argument("--job-id", required=True)

    submit_parser = subparsers.add_parser("task-submit")
    _repo_argument(submit_parser)
    _config_argument(submit_parser)
    submit_parser.add_argument("--task", required=True)
    submit_parser.add_argument("--criterion", action="append", required=True)
    submit_parser.add_argument("--idempotency-key", required=True)
    submit_parser.add_argument("--run-id")
    _policy_choice(submit_parser)

    worker_parser = subparsers.add_parser("worker-once")
    _repo_argument(worker_parser)
    worker_parser.add_argument("--worker-id", required=True)
    worker_parser.add_argument("--lease-seconds", type=float, default=3600.0)

    verify_parser = subparsers.add_parser("verify")
    _repo_argument(verify_parser)
    _config_argument(verify_parser)

    agent_parser = subparsers.add_parser("agent-run")
    _repo_argument(agent_parser)
    _config_argument(agent_parser)
    agent_parser.add_argument("--task", required=True)
    agent_parser.add_argument("--criterion", action="append", required=True)
    agent_parser.add_argument("--run-id")
    _policy_choice(agent_parser)

    benchmark_parser = subparsers.add_parser("benchmark-run")
    _repo_argument(benchmark_parser)
    _config_argument(benchmark_parser)
    benchmark_parser.add_argument("--dataset", required=True)
    _policy_choice(benchmark_parser)

    evolve_parser = subparsers.add_parser("evolve")
    _repo_argument(evolve_parser)
    _config_argument(evolve_parser)
    evolve_parser.add_argument("--train-dataset", required=True)
    evolve_parser.add_argument("--holdout-dataset", required=True)
    evolve_parser.add_argument("--evolution-id")
    _policy_choice(evolve_parser, option="--seed-policy")

    approve_parser = subparsers.add_parser("policy-approve")
    _repo_argument(approve_parser)
    approve_parser.add_argument("--evolution-id", required=True)
    approve_parser.add_argument("--note", default="")

    activate_parser = subparsers.add_parser("policy-activate")
    _repo_argument(activate_parser)
    activate_parser.add_argument("--evolution-id", required=True)
    activate_parser.add_argument("--approval", required=True)

    show_parser = subparsers.add_parser("policy-show")
    _repo_argument(show_parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _main(argv)
    except (
        ConfigError,
        json.JSONDecodeError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        _print_json(
            {
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
            stream=sys.stderr,
        )
        return 2


def _main(argv: Sequence[str] | None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo).resolve()
    if args.command == "init":
        database_path = authority_database_path(repo_root)
        Database(database_path).initialize()
        _print_json({"database_path": str(database_path), "status": "initialized"})
        return 0
    if args.command == "queue-enqueue":
        payload = _json_object(args.payload_json, "--payload-json")
        job = JobQueue(authority_database_path(repo_root)).enqueue(
            kind=args.kind,
            payload=payload,
            idempotency_key=args.idempotency_key,
        )
        _print_json(job.to_dict())
        return 0
    if args.command == "queue-claim":
        job = JobQueue(authority_database_path(repo_root)).claim(
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
        )
        _print_json(job.to_dict() if job is not None else {"job": None})
        return 0
    if args.command == "queue-heartbeat":
        job = JobQueue(authority_database_path(repo_root)).heartbeat(
            args.job_id,
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
        )
        _print_json(job.to_dict())
        return 0
    if args.command == "queue-finish":
        result = _json_object(args.result_json, "--result-json")
        queue = JobQueue(authority_database_path(repo_root))
        if args.status == "completed":
            job = queue.complete(
                args.job_id,
                worker_id=args.worker_id,
                result=result,
            )
        else:
            job = queue.fail(
                args.job_id,
                worker_id=args.worker_id,
                result=result,
            )
        _print_json(job.to_dict())
        return 0
    if args.command == "queue-get":
        job = JobQueue(authority_database_path(repo_root)).get(args.job_id)
        _print_json(job.to_dict() if job is not None else {"job": None})
        return 0
    if args.command == "task-submit":
        config_path = _repo_path(repo_root, args.config)
        config_relative = config_path.relative_to(repo_root).as_posix()
        job = JobQueue(authority_database_path(repo_root)).enqueue(
            kind="coding-agent",
            payload={
                "task": args.task,
                "criteria": args.criterion,
                "config": config_relative,
                "policy": args.policy,
                "run_id": args.run_id,
            },
            idempotency_key=args.idempotency_key,
        )
        _print_json(job.to_dict())
        return 0
    if args.command == "worker-once":
        job = CodingWorker(repo_root).run_once(
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
        )
        _print_json(job.to_dict() if job is not None else {"job": None})
        if job is None:
            return 0
        return 0 if job.status.value == "completed" else 1
    if args.command == "verify":
        config_path = _repo_path(repo_root, args.config)
        try:
            verification = load_harness_config(config_path).verification
        except ConfigError as harness_error:
            try:
                verification = load_verification_config(config_path)
            except ConfigError:
                raise harness_error
        receipt = BoundedVerifier(verification_artifact_root(repo_root)).verify(
            repo_root,
            verification.selected_commands,
        )
        _print_json(receipt.to_dict())
        return 0 if receipt.passed else 1
    if args.command == "agent-run":
        config_path = _repo_path(repo_root, args.config)
        config = load_harness_config(config_path)
        policy = _resolve_policy(repo_root, config, args.policy)
        provider = OpenAICompatibleProvider(config.provider)
        result = LocalCodingAgent(
            provider=provider,
            verifier=BoundedVerifier(verification_artifact_root(repo_root)),
            agent_artifact_root=agent_artifact_root(repo_root),
            limits=config.limits,
            cadence=policy.cadence,
            strategy_prompt=policy.strategy_prompt,
            protected_write_paths=(config_path,),
        ).run(
            repo_root,
            AgentTask(args.task, tuple(args.criterion)),
            config.verification.selected_commands,
            run_id=args.run_id,
        )
        _print_json(result.to_dict())
        return 0 if result.success else 1
    if args.command == "benchmark-run":
        config = load_harness_config(_repo_path(repo_root, args.config))
        policy = _resolve_policy(repo_root, config, args.policy)
        examples = load_benchmark_dataset(_repo_path(repo_root, args.dataset))
        evaluator = CodingAgentBenchmarkEvaluator(
            provider=OpenAICompatibleProvider(config.provider),
            limits=config.limits,
            rollout_root=evolution_artifact_root(repo_root) / "benchmark-rollouts",
        )
        aggregate = evaluate_policy(policy, examples, evaluator)
        _print_json(
            {
                "policy": policy.to_dict(),
                "evaluation": aggregate.to_dict(),
            }
        )
        return 0 if aggregate.success_rate == 1.0 else 1
    if args.command == "evolve":
        config = load_harness_config(_repo_path(repo_root, args.config))
        seed = _resolve_policy(repo_root, config, args.seed_policy)
        trainset = load_benchmark_dataset(
            _repo_path(repo_root, args.train_dataset)
        )
        holdout = load_benchmark_dataset(
            _repo_path(repo_root, args.holdout_dataset)
        )
        evolution_id = args.evolution_id or f"evolution-{uuid.uuid4().hex}"
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
        _print_json(result.to_dict())
        return 0 if result.accepted else 1
    if args.command == "policy-approve":
        result_path = _evolution_result_path(repo_root, args.evolution_id)
        approval = PolicyRegistry(policy_root(repo_root)).approve(
            result_path,
            note=args.note,
        )
        _print_json({"approval_path": str(approval), "status": "approved"})
        return 0
    if args.command == "policy-activate":
        result_path = _evolution_result_path(repo_root, args.evolution_id)
        approval = Path(args.approval).resolve()
        registry_root = policy_root(repo_root)
        contained_path(registry_root, approval)
        active = PolicyRegistry(registry_root).activate(result_path, approval)
        _print_json({"active_policy_path": str(active), "status": "activated"})
        return 0
    if args.command == "policy-show":
        policy = PolicyRegistry(policy_root(repo_root)).load_active()
        _print_json(policy.to_dict() if policy is not None else {"policy": None})
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


def _resolve_policy(
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


def _repo_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", default=".")


def _config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="sisyphus-harness.toml")


def _policy_choice(
    parser: argparse.ArgumentParser,
    *,
    option: str = "--policy",
) -> None:
    parser.add_argument(option, choices=("config", "active"), default="config")


def _repo_path(repo_root: Path, raw: str) -> Path:
    return contained_path(repo_root, raw)


def _evolution_result_path(repo_root: Path, evolution_id: str) -> Path:
    validated_id = validate_evolution_id(evolution_id)
    return contained_path(
        evolution_artifact_root(repo_root),
        Path(validated_id) / "result.json",
        require_relative=True,
    )


def _json_object(raw: str, field: str) -> dict[str, object]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"{field} must decode to an object")
    return payload


def _print_json(payload: object, *, stream=None) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True), file=stream)


if __name__ == "__main__":
    raise SystemExit(main())
