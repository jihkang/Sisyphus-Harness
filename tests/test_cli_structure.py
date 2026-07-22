from __future__ import annotations

import unittest

from sisyphus_harness import cli
from sisyphus_harness.interfaces.cli.dispatcher import COMMAND_ROUTES
from sisyphus_harness.interfaces.cli.parser import build_parser


PARSER_CASES: tuple[tuple[str, tuple[str, ...], dict[str, object]], ...] = (
    ("init", (), {}),
    ("verifier-assets-create", ("--source", "assets"), {"source": "assets"}),
    (
        "verification-profile-create",
        ("--profile-id", "profile", "--asset-bundle-id", "bundle"),
        {
            "config": "sisyphus-harness.toml",
            "profile_id": "profile",
            "asset_bundle_id": "bundle",
        },
    ),
    (
        "queue-enqueue",
        ("--kind", "coding", "--idempotency-key", "key", "--payload-json", "{}"),
        {"kind": "coding", "idempotency_key": "key", "payload_json": "{}"},
    ),
    (
        "queue-claim",
        ("--worker-id", "worker"),
        {"worker_id": "worker", "lease_seconds": 60.0},
    ),
    (
        "queue-heartbeat",
        ("--job-id", "job", "--worker-id", "worker"),
        {"job_id": "job", "worker_id": "worker", "lease_seconds": 60.0},
    ),
    (
        "queue-finish",
        (
            "--job-id",
            "job",
            "--worker-id",
            "worker",
            "--status",
            "completed",
            "--result-json",
            "{}",
        ),
        {
            "job_id": "job",
            "worker_id": "worker",
            "status": "completed",
            "result_json": "{}",
        },
    ),
    ("queue-get", ("--job-id", "job"), {"job_id": "job"}),
    (
        "task-submit",
        (
            "--task",
            "task",
            "--criterion",
            "criterion",
            "--idempotency-key",
            "key",
        ),
        {
            "config": "sisyphus-harness.toml",
            "task": "task",
            "criterion": ["criterion"],
            "idempotency_key": "key",
            "run_id": None,
            "policy": "config",
        },
    ),
    ("task-status", ("--job-id", "job"), {"job_id": "job"}),
    (
        "task-adjudicate",
        (
            "--job-id",
            "job",
            "--profile",
            "profile.json",
            "--contract",
            "contract.json",
            "--run-id",
            "run",
        ),
        {
            "config": "sisyphus-harness.toml",
            "job_id": "job",
            "profile": "profile.json",
            "contract": "contract.json",
            "run_id": "run",
            "producer_authority": "control.verifier.local",
        },
    ),
    (
        "worker-once",
        ("--worker-id", "worker"),
        {"worker_id": "worker", "lease_seconds": 3600.0},
    ),
    (
        "verify",
        (),
        {"config": "sisyphus-harness.toml", "trusted_in_process": False},
    ),
    (
        "agent-run",
        ("--task", "task", "--criterion", "criterion"),
        {
            "config": "sisyphus-harness.toml",
            "task": "task",
            "criterion": ["criterion"],
            "run_id": None,
            "policy": "config",
        },
    ),
    (
        "benchmark-run",
        ("--dataset", "dataset.json"),
        {
            "config": "sisyphus-harness.toml",
            "dataset": "dataset.json",
            "policy": "config",
        },
    ),
    (
        "evolve",
        ("--train-dataset", "train.json", "--holdout-dataset", "holdout.json"),
        {
            "config": "sisyphus-harness.toml",
            "train_dataset": "train.json",
            "holdout_dataset": "holdout.json",
            "evolution_id": None,
            "seed_policy": "config",
        },
    ),
    (
        "policy-approve",
        ("--evolution-id", "evolution"),
        {"evolution_id": "evolution", "note": ""},
    ),
    (
        "policy-activate",
        ("--evolution-id", "evolution", "--approval", "approval.json"),
        {"evolution_id": "evolution", "approval": "approval.json"},
    ),
    ("policy-show", (), {}),
    ("graph-init", (), {}),
    ("graph-put-node", ("--node-json", "{}"), {"node_json": "{}"}),
    ("graph-put-edge", ("--edge-json", "{}"), {"edge_json": "{}"}),
    (
        "graph-search",
        ("--anchor-id", "anchor", "--query", "query"),
        {"anchor_id": "anchor", "query": "query", "max_depth": 3, "limit": 20},
    ),
    (
        "graph-dependencies",
        ("--task-id", "task"),
        {"task_id": "task", "max_depth": 3},
    ),
    (
        "graph-next",
        ("--anchor-id", "anchor"),
        {
            "anchor_id": "anchor",
            "query": None,
            "max_depth": 3,
            "limit": 20,
            "dependency_max_depth": 3,
        },
    ),
)


class CliStructureTests(unittest.TestCase):
    def test_all_command_names_arguments_and_defaults_are_preserved(self) -> None:
        parser = build_parser()
        expected_commands = {case[0] for case in PARSER_CASES}
        self.assertEqual(len(expected_commands), 25)
        self.assertEqual(set(COMMAND_ROUTES), expected_commands)
        for command, arguments, expected in PARSER_CASES:
            with self.subTest(command=command):
                parsed = parser.parse_args((command, *arguments))
                self.assertEqual(
                    vars(parsed),
                    {"command": command, "repo": ".", **expected},
                )

    def test_legacy_module_reexports_the_parser(self) -> None:
        self.assertIs(cli.build_parser, build_parser)


if __name__ == "__main__":
    unittest.main()
