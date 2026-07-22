from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from .handlers.execution import handle_execution
from .handlers.knowledge import handle_knowledge
from .handlers.policy import handle_policy
from .handlers.queue import handle_queue
from .handlers.setup import handle_setup
from .handlers.task import handle_task
from .result import CliResult


CommandHandler = Callable[[argparse.Namespace, Path], CliResult]


def _routes() -> dict[str, CommandHandler]:
    groups = (
        (
            handle_setup,
            (
                "init",
                "verifier-assets-create",
                "verification-profile-create",
            ),
        ),
        (
            handle_queue,
            (
                "queue-enqueue",
                "queue-claim",
                "queue-heartbeat",
                "queue-finish",
                "queue-get",
            ),
        ),
        (
            handle_task,
            (
                "task-submit",
                "task-status",
                "task-adjudicate",
                "worker-once",
            ),
        ),
        (
            handle_execution,
            ("verify", "agent-run", "benchmark-run", "evolve"),
        ),
        (
            handle_policy,
            ("policy-approve", "policy-activate", "policy-show"),
        ),
        (
            handle_knowledge,
            (
                "graph-init",
                "graph-put-node",
                "graph-put-edge",
                "graph-search",
                "graph-dependencies",
                "graph-next",
            ),
        ),
    )
    return {
        command: handler
        for handler, commands in groups
        for command in commands
    }


COMMAND_ROUTES = _routes()


def dispatch(args: argparse.Namespace) -> CliResult:
    try:
        handler = COMMAND_ROUTES[args.command]
    except KeyError as exc:
        raise AssertionError(f"unhandled command: {args.command}") from exc
    return handler(args, Path(args.repo).resolve())
