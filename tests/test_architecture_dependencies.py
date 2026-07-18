from __future__ import annotations

import ast
from pathlib import Path
import unittest

from sisyphus_harness import agent, config, evolution, models, workspace
from sisyphus_harness.contracts import (
    AgentResult,
    AgentTask,
    CadencePolicy,
    CandidateError,
    CandidatePolicy,
    CommandResult,
    CommandSpec,
    EvaluationAggregate,
    EvaluationObservation,
    EvolutionResult,
    VerificationReceipt,
    WorkspaceSnapshot,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "sisyphus_harness"


class ArchitectureDependencyTests(unittest.TestCase):
    def test_legacy_import_paths_reexport_contract_types(self) -> None:
        aliases = (
            (agent.AgentTask, AgentTask),
            (agent.AgentResult, AgentResult),
            (config.CadencePolicy, CadencePolicy),
            (evolution.CandidateError, CandidateError),
            (evolution.CandidatePolicy, CandidatePolicy),
            (evolution.EvaluationObservation, EvaluationObservation),
            (evolution.EvaluationAggregate, EvaluationAggregate),
            (evolution.EvolutionResult, EvolutionResult),
            (models.CommandSpec, CommandSpec),
            (models.CommandResult, CommandResult),
            (models.VerificationReceipt, VerificationReceipt),
            (workspace.WorkspaceSnapshot, WorkspaceSnapshot),
        )
        for legacy, contract in aliases:
            with self.subTest(contract=contract.__name__):
                self.assertIs(legacy, contract)

    def test_contracts_do_not_import_runtime_modules(self) -> None:
        for path in sorted((PACKAGE_ROOT / "contracts").glob("*.py")):
            with self.subTest(path=path.name):
                for node in _imports(path):
                    if isinstance(node, ast.ImportFrom):
                        self.assertLessEqual(
                            node.level,
                            1,
                            f"{path.name} imports outside the contracts package",
                        )
                        if node.level == 0 and node.module is not None:
                            self.assertFalse(
                                node.module.startswith("sisyphus_harness"),
                                f"{path.name} imports runtime module {node.module}",
                            )
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertFalse(
                                alias.name.startswith("sisyphus_harness"),
                                f"{path.name} imports runtime module {alias.name}",
                            )

    def test_runtime_dependency_direction_is_acyclic(self) -> None:
        forbidden = {
            "agent.py": {"benchmarks", "evolution", "policy", "queue", "worker"},
            "benchmarks.py": {"agent", "verifier"},
            "cli.py": {"agent", "verifier"},
            "verifier.py": {
                "agent",
                "benchmarks",
                "evolution",
                "policy",
                "queue",
                "worker",
            },
            "evolution.py": {
                "agent",
                "benchmarks",
                "policy",
                "queue",
                "verifier",
                "worker",
            },
            "worker.py": {"agent", "verifier"},
        }
        for filename, blocked in forbidden.items():
            path = PACKAGE_ROOT / filename
            with self.subTest(path=filename):
                imported = {
                    node.module.split(".", 1)[0]
                    for node in _imports(path)
                    if isinstance(node, ast.ImportFrom)
                    and node.level == 1
                    and node.module
                }
                self.assertFalse(
                    imported.intersection(blocked),
                    f"{filename} imports forbidden modules: "
                    f"{sorted(imported.intersection(blocked))}",
                )


def _imports(path: Path) -> list[ast.Import | ast.ImportFrom]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]


if __name__ == "__main__":
    unittest.main()
