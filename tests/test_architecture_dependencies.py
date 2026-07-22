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
        for path in sorted((PACKAGE_ROOT / "contracts").rglob("*.py")):
            with self.subTest(path=path.relative_to(PACKAGE_ROOT)):
                package_depth = len(path.relative_to(PACKAGE_ROOT).parent.parts)
                for node in _imports(path):
                    if isinstance(node, ast.ImportFrom):
                        self.assertLessEqual(
                            node.level,
                            package_depth,
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

    def test_task_outcome_write_authority_is_not_available_to_worker(self) -> None:
        worker_source = (PACKAGE_ROOT / "worker.py").read_text(encoding="utf-8")
        for forbidden in (
            "TaskOutcome",
            "TaskOutcomeAuthorityPort",
            "publish_task_outcome",
            "task_outcomes",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, worker_source)

        writers = [
            path.relative_to(PACKAGE_ROOT).as_posix()
            for path in PACKAGE_ROOT.rglob("*.py")
            if "INSERT INTO task_outcomes" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(writers, ["infra/control_outcomes.py"])

    def test_control_outcome_service_depends_on_ports_not_infrastructure(self) -> None:
        path = PACKAGE_ROOT / "services" / "control_outcomes.py"
        imported = {
            node.module.split(".", 1)[0]
            for node in _imports(path)
            if isinstance(node, ast.ImportFrom)
            and node.level == 2
            and node.module
        }
        self.assertFalse(
            imported.intersection({"database", "infra", "queue", "worker"})
        )

    def test_docker_verifier_facade_keeps_runtime_responsibilities_extracted(
        self,
    ) -> None:
        facade_path = PACKAGE_ROOT / "adapters" / "docker_verifier.py"
        tree = ast.parse(
            facade_path.read_text(encoding="utf-8"),
            filename=str(facade_path),
        )
        facade = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "DockerVerifierTransport"
        )
        self.assertLessEqual(facade.end_lineno - facade.lineno + 1, 325)
        method_names = {
            node.name for node in facade.body if isinstance(node, ast.FunctionDef)
        }
        self.assertFalse(
            method_names.intersection(
                {"_collect_output_with_selector", "_collect_output_with_threads"}
            )
        )

        direct_imports = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertFalse(
            direct_imports.intersection(
                {"hashlib", "selectors", "signal", "stat", "threading"}
            )
        )

        collaborators = {
            "docker_bundle_view.py": "prepare_bundle_view",
            "docker_evidence.py": "DockerEvidencePublisher",
            "docker_host_verification.py": "DockerHostVerifier",
            "docker_runtime.py": "DockerRuntime",
        }
        for filename, symbol in collaborators.items():
            with self.subTest(filename=filename):
                source = (PACKAGE_ROOT / "adapters" / filename).read_text(
                    encoding="utf-8"
                )
                self.assertIn(symbol, source)


def _imports(path: Path) -> list[ast.Import | ast.ImportFrom]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]


if __name__ == "__main__":
    unittest.main()
