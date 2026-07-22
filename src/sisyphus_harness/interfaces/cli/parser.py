from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sisyphus-harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    _repo_argument(init_parser)

    asset_parser = subparsers.add_parser("verifier-assets-create")
    _repo_argument(asset_parser)
    asset_parser.add_argument("--source", required=True)

    profile_parser = subparsers.add_parser("verification-profile-create")
    _repo_argument(profile_parser)
    _config_argument(profile_parser)
    profile_parser.add_argument("--profile-id", required=True)
    profile_parser.add_argument("--asset-bundle-id", required=True)

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
    finish_parser.add_argument(
        "--status",
        choices=("completed", "failed"),
        required=True,
    )
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

    status_parser = subparsers.add_parser("task-status")
    _repo_argument(status_parser)
    status_parser.add_argument("--job-id", required=True)

    adjudicate_parser = subparsers.add_parser("task-adjudicate")
    _repo_argument(adjudicate_parser)
    _config_argument(adjudicate_parser)
    adjudicate_parser.add_argument("--job-id", required=True)
    adjudicate_parser.add_argument("--profile", required=True)
    adjudicate_parser.add_argument("--contract", required=True)
    adjudicate_parser.add_argument("--run-id", required=True)
    adjudicate_parser.add_argument(
        "--producer-authority",
        default="control.verifier.local",
    )

    worker_parser = subparsers.add_parser("worker-once")
    _repo_argument(worker_parser)
    worker_parser.add_argument("--worker-id", required=True)
    worker_parser.add_argument("--lease-seconds", type=float, default=3600.0)

    verify_parser = subparsers.add_parser("verify")
    _repo_argument(verify_parser)
    _config_argument(verify_parser)
    verify_parser.add_argument(
        "--trusted-in-process",
        action="store_true",
        help="allow a legacy verification-only config to execute on the host",
    )

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

    graph_init_parser = subparsers.add_parser("graph-init")
    _repo_argument(graph_init_parser)

    graph_node_parser = subparsers.add_parser("graph-put-node")
    _repo_argument(graph_node_parser)
    graph_node_parser.add_argument("--node-json", required=True)

    graph_edge_parser = subparsers.add_parser("graph-put-edge")
    _repo_argument(graph_edge_parser)
    graph_edge_parser.add_argument("--edge-json", required=True)

    graph_search_parser = subparsers.add_parser("graph-search")
    _repo_argument(graph_search_parser)
    graph_search_parser.add_argument("--anchor-id", required=True)
    graph_search_parser.add_argument("--query", required=True)
    _graph_query_limits(graph_search_parser)

    graph_dependencies_parser = subparsers.add_parser("graph-dependencies")
    _repo_argument(graph_dependencies_parser)
    graph_dependencies_parser.add_argument("--task-id", required=True)
    graph_dependencies_parser.add_argument("--max-depth", type=int, default=3)

    graph_next_parser = subparsers.add_parser("graph-next")
    _repo_argument(graph_next_parser)
    graph_next_parser.add_argument("--anchor-id", required=True)
    graph_next_parser.add_argument("--query")
    _graph_query_limits(graph_next_parser)
    graph_next_parser.add_argument(
        "--dependency-max-depth",
        type=int,
        default=3,
    )
    return parser


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


def _graph_query_limits(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--limit", type=int, default=20)
