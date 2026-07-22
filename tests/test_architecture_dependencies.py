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

    def test_local_coding_agent_facade_keeps_loop_responsibilities_extracted(
        self,
    ) -> None:
        facade_path = PACKAGE_ROOT / "agent.py"
        tree = ast.parse(
            facade_path.read_text(encoding="utf-8"),
            filename=str(facade_path),
        )
        facade = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "LocalCodingAgent"
        )
        self.assertLessEqual(facade.end_lineno - facade.lineno + 1, 200)
        run_method = next(
            node
            for node in facade.body
            if isinstance(node, ast.FunctionDef) and node.name == "run"
        )
        self.assertLessEqual(run_method.end_lineno - run_method.lineno + 1, 100)
        self.assertFalse(
            any(
                isinstance(node, (ast.For, ast.While))
                for node in ast.walk(run_method)
            )
        )
        method_names = {
            node.name for node in facade.body if isinstance(node, ast.FunctionDef)
        }
        self.assertFalse(
            method_names.intersection(
                {"_finish", "_should_compact", "_step_payload"}
            )
        )

        direct_imports = {
            imported
            for node in tree.body
            for imported in _absolute_import_roots(node)
        }
        self.assertFalse(
            direct_imports.intersection({"datetime", "hashlib", "json"})
        )

        collaborators = {
            "agent_artifacts.py": "AgentRunRecorder",
            "agent_context.py": "AgentPromptRenderer",
            "agent_loop.py": "AgentRunLoop",
            "agent_state.py": "AgentRunState",
            "agent_transitions.py": "AgentVerificationTransitionHandler",
        }
        imported_symbols = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.level == 1
            for alias in node.names
        }
        self.assertFalse(set(collaborators.values()).difference(imported_symbols))
        for filename, symbol in collaborators.items():
            with self.subTest(filename=filename):
                source = (PACKAGE_ROOT / filename).read_text(encoding="utf-8")
                self.assertIn(symbol, source)

        for filename in collaborators:
            component_tree = ast.parse(
                (PACKAGE_ROOT / filename).read_text(encoding="utf-8"),
                filename=filename,
            )
            for node in component_tree.body:
                if isinstance(node, ast.ClassDef):
                    with self.subTest(filename=filename, class_name=node.name):
                        self.assertLessEqual(
                            node.end_lineno - node.lineno + 1,
                            325,
                        )

    def test_cli_facade_keeps_command_responsibilities_extracted(self) -> None:
        facade_path = PACKAGE_ROOT / "cli.py"
        source = facade_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(facade_path))
        self.assertLessEqual(len(source.splitlines()), 60)
        main_delegate = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_main"
        )
        self.assertLessEqual(
            main_delegate.end_lineno - main_delegate.lineno + 1,
            10,
        )
        self.assertFalse(
            any(
                isinstance(node, (ast.For, ast.If, ast.Match, ast.While))
                for node in ast.walk(main_delegate)
            )
        )

        direct_imports = {
            imported
            for node in tree.body
            for imported in _absolute_import_roots(node)
        }
        self.assertFalse(
            direct_imports.intersection(
                {
                    "benchmarks",
                    "evolution",
                    "knowledge_graph",
                    "policy",
                    "provider",
                    "queue",
                    "runtime",
                    "worker",
                }
            )
        )
        relative_modules = {
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.level == 1
        }
        self.assertTrue(
            {
                "interfaces.cli.dispatcher",
                "interfaces.cli.parser",
                "interfaces.cli.renderers",
            }.issubset(relative_modules)
        )

        cli_root = PACKAGE_ROOT / "interfaces" / "cli"
        for path in sorted(cli_root.rglob("*.py")):
            with self.subTest(path=path.relative_to(PACKAGE_ROOT)):
                component_source = path.read_text(encoding="utf-8")
                self.assertLessEqual(len(component_source.splitlines()), 220)
                if path.parent.name == "handlers":
                    self.assertNotIn("render_json(", component_source)
                    self.assertNotIn("print(", component_source)

    def test_knowledge_facades_keep_query_and_persistence_responsibilities_extracted(
        self,
    ) -> None:
        graph_path = PACKAGE_ROOT / "knowledge_graph.py"
        graph_source = graph_path.read_text(encoding="utf-8")
        graph_tree = ast.parse(graph_source, filename=str(graph_path))
        self.assertLessEqual(len(graph_source.splitlines()), 100)
        graph_class = next(
            node
            for node in graph_tree.body
            if isinstance(node, ast.ClassDef) and node.name == "KnowledgeGraph"
        )
        for method in (
            node
            for node in graph_class.body
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("__")
        ):
            with self.subTest(graph_method=method.name):
                self.assertFalse(
                    any(
                        isinstance(node, (ast.For, ast.While))
                        for node in ast.walk(method)
                    )
                )
        graph_imports = {
            node.module
            for node in graph_tree.body
            if isinstance(node, ast.ImportFrom) and node.level == 1
        }
        self.assertTrue(
            {
                "knowledge_dependencies",
                "knowledge_mutations",
                "knowledge_planning",
                "knowledge_search",
                "ports.knowledge",
            }.issubset(graph_imports)
        )

        index_path = PACKAGE_ROOT / "infra" / "knowledge_index.py"
        index_source = index_path.read_text(encoding="utf-8")
        index_tree = ast.parse(index_source, filename=str(index_path))
        self.assertLessEqual(len(index_source.splitlines()), 80)
        self.assertNotIn("SELECT ", index_source)
        self.assertNotIn("INSERT INTO", index_source)
        index_class = next(
            node
            for node in index_tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "SQLiteKnowledgeIndex"
        )
        self.assertEqual(
            [base.id for base in index_class.bases if isinstance(base, ast.Name)],
            ["SQLiteKnowledgeDatabase"],
        )

        component_paths = sorted(PACKAGE_ROOT.glob("knowledge_*.py")) + sorted(
            (PACKAGE_ROOT / "infra").glob("knowledge_*.py")
        )
        for path in component_paths:
            with self.subTest(component=path.relative_to(PACKAGE_ROOT)):
                source = path.read_text(encoding="utf-8")
                self.assertLessEqual(len(source.splitlines()), 280)
                if path.parent == PACKAGE_ROOT:
                    self.assertNotIn("import sqlite3", source)

    def test_workspace_tools_facade_keeps_operational_responsibilities_extracted(
        self,
    ) -> None:
        facade_path = PACKAGE_ROOT / "tools.py"
        source = facade_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(facade_path))
        self.assertLessEqual(len(source.splitlines()), 100)
        facade = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "WorkspaceTools"
        )
        execute = next(
            node
            for node in facade.body
            if isinstance(node, ast.FunctionDef) and node.name == "execute"
        )
        self.assertFalse(
            any(isinstance(node, (ast.For, ast.While)) for node in ast.walk(execute))
        )
        self.assertNotIn("subprocess.run", source)
        for forbidden in ("hashlib", "os", "stat", "tempfile"):
            with self.subTest(forbidden_import=forbidden):
                self.assertNotIn(forbidden, _absolute_import_roots_from_tree(tree))

        relative_modules = {
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.level == 1
        }
        self.assertTrue(
            {
                "workspace_tool_contracts",
                "workspace_tool_io",
                "workspace_tool_mutations",
                "workspace_tool_paths",
                "workspace_tool_queries",
            }.issubset(relative_modules)
        )

        components = sorted(PACKAGE_ROOT.glob("workspace_tool_*.py"))
        self.assertEqual(len(components), 6)
        for path in components:
            with self.subTest(component=path.name):
                component_source = path.read_text(encoding="utf-8")
                self.assertLessEqual(len(component_source.splitlines()), 220)
        arguments = (PACKAGE_ROOT / "workspace_tool_arguments.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("import os", arguments)
        self.assertNotIn("import subprocess", arguments)


def _imports(path: Path) -> list[ast.Import | ast.ImportFrom]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]


def _absolute_import_roots(node: ast.stmt) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name.split(".", 1)[0] for alias in node.names}
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        return {node.module.split(".", 1)[0]}
    return set()


def _absolute_import_roots_from_tree(tree: ast.Module) -> set[str]:
    return {
        imported
        for node in tree.body
        for imported in _absolute_import_roots(node)
    }


if __name__ == "__main__":
    unittest.main()
