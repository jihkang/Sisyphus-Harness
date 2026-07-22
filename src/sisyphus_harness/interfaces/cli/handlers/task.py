from __future__ import annotations

import argparse
from pathlib import Path

from ....authority import authority_database_path, workspace_bundle_root
from ....config import load_harness_config
from ....contracts.evidence_contract import EvidenceContract
from ....contracts.verification_service import VerificationProfile
from ....infra.control_outcomes import SQLiteTaskOutcomeAuthority
from ....infra.workspace_bundle import FilesystemWorkspaceBundleStore
from ....ports.control_outcomes import TaskOutcomeRequest
from ....queue import JobQueue
from ....runtime import build_control_task_outcome_service
from ....worker import CodingWorker
from ..io import repo_path, sha256_path, strict_json_file
from ..policy_selection import resolve_policy
from ..result import CliResult


def handle_task(args: argparse.Namespace, repo_root: Path) -> CliResult:
    if args.command == "task-submit":
        config_path = repo_path(repo_root, args.config)
        config_relative = config_path.relative_to(repo_root).as_posix()
        config = load_harness_config(config_path)
        policy = resolve_policy(repo_root, config, args.policy)
        config_digest = sha256_path(config_path)
        workspace_bundle = FilesystemWorkspaceBundleStore(
            workspace_bundle_root(repo_root)
        ).create(repo_root)
        job = JobQueue(authority_database_path(repo_root)).enqueue(
            kind="coding-agent",
            payload={
                "task": args.task,
                "criteria": args.criterion,
                "config": config_relative,
                "policy": args.policy,
                "run_id": args.run_id,
                "workspace_bundle": workspace_bundle.to_dict(),
                "config_sha256": config_digest,
                "policy_snapshot": policy.to_dict(),
            },
            idempotency_key=args.idempotency_key,
        )
        return CliResult(job.to_dict())
    if args.command == "task-status":
        database_path = authority_database_path(repo_root)
        queue = JobQueue(database_path)
        authority = SQLiteTaskOutcomeAuthority(database_path)
        job = queue.get(args.job_id)
        attempt = authority.get_attempt_finished(args.job_id)
        outcome = authority.get_task_outcome(args.job_id)
        return CliResult(
            {
                "attempt_finished": (
                    attempt.to_dict() if attempt is not None else None
                ),
                "job": job.to_dict() if job is not None else None,
                "task_outcome": outcome.to_dict() if outcome is not None else None,
            }
        )
    if args.command == "task-adjudicate":
        config = load_harness_config(repo_path(repo_root, args.config))
        profile = VerificationProfile.from_dict(
            strict_json_file(
                repo_path(repo_root, args.profile),
                label="verification profile",
            )
        )
        contract = EvidenceContract.from_dict(
            strict_json_file(
                repo_path(repo_root, args.contract),
                label="evidence contract",
            )
        )
        outcome = build_control_task_outcome_service(repo_root, config).adjudicate(
            TaskOutcomeRequest(
                job_id=args.job_id,
                profile=profile,
                contract=contract,
                run_id=args.run_id,
                producer_authority=args.producer_authority,
            )
        )
        return CliResult(
            outcome.to_dict(),
            exit_code=0 if outcome.decision.value == "passed" else 1,
        )
    if args.command == "worker-once":
        job = CodingWorker(repo_root).run_once(
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
        )
        if job is None:
            return CliResult({"job": None})
        return CliResult(
            job.to_dict(),
            exit_code=0 if job.status.value == "completed" else 1,
        )
    raise AssertionError(f"unhandled task command: {args.command}")
