from __future__ import annotations

import argparse
from pathlib import Path

from ....authority import policy_root
from ....policy import PolicyRegistry
from ....workspace import contained_path
from ..policy_selection import evolution_result_path
from ..result import CliResult


def handle_policy(args: argparse.Namespace, repo_root: Path) -> CliResult:
    if args.command == "policy-show":
        policy = PolicyRegistry(policy_root(repo_root)).load_active()
        return CliResult(
            policy.to_dict() if policy is not None else {"policy": None}
        )
    if args.command not in ("policy-approve", "policy-activate"):
        raise AssertionError(f"unhandled policy command: {args.command}")

    if args.command == "policy-approve":
        result_path = evolution_result_path(repo_root, args.evolution_id)
        approval = PolicyRegistry(policy_root(repo_root)).approve(
            result_path,
            note=args.note,
        )
        return CliResult(
            {"approval_path": str(approval), "status": "approved"}
        )
    result_path = evolution_result_path(repo_root, args.evolution_id)
    approval = Path(args.approval).resolve()
    registry_root = policy_root(repo_root)
    contained_path(registry_root, approval)
    active = PolicyRegistry(registry_root).activate(result_path, approval)
    return CliResult(
        {"active_policy_path": str(active), "status": "activated"}
    )
