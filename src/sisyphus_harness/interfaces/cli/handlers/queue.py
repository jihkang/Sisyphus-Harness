from __future__ import annotations

import argparse
from pathlib import Path

from ....authority import authority_database_path
from ....queue import JobQueue
from ..io import json_object
from ..result import CliResult


def handle_queue(args: argparse.Namespace, repo_root: Path) -> CliResult:
    if args.command == "queue-enqueue":
        payload = json_object(args.payload_json, "--payload-json")
        job = JobQueue(authority_database_path(repo_root)).enqueue(
            kind=args.kind,
            payload=payload,
            idempotency_key=args.idempotency_key,
        )
        return CliResult(job.to_dict())
    if args.command == "queue-claim":
        job = JobQueue(authority_database_path(repo_root)).claim(
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
        )
        return CliResult(job.to_dict() if job is not None else {"job": None})
    if args.command == "queue-heartbeat":
        job = JobQueue(authority_database_path(repo_root)).heartbeat(
            args.job_id,
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
        )
        return CliResult(job.to_dict())
    if args.command == "queue-finish":
        result = json_object(args.result_json, "--result-json")
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
        return CliResult(job.to_dict())
    if args.command == "queue-get":
        job = JobQueue(authority_database_path(repo_root)).get(args.job_id)
        return CliResult(job.to_dict() if job is not None else {"job": None})
    raise AssertionError(f"unhandled queue command: {args.command}")
