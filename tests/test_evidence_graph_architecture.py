from __future__ import annotations

import ast
from pathlib import Path
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "sisyphus_harness"


class EvidenceGraphArchitectureTests(unittest.TestCase):
    def test_pure_decision_domains_depend_only_on_contracts(self) -> None:
        evidence_path = PACKAGE_ROOT / "evidence_contract.py"
        evidence_imports = _internal_imports(evidence_path)
        self.assertTrue(
            all(
                name == "contracts" or name.startswith("contracts.")
                for name in evidence_imports
            ),
            "evidence_contract.py imports non-contract runtime modules: "
            f"{sorted(evidence_imports)}",
        )

        knowledge_paths = sorted(PACKAGE_ROOT.glob("knowledge_*.py"))
        self.assertTrue(knowledge_paths)
        for path in knowledge_paths:
            internal = _internal_imports(path)
            forbidden = {
                name
                for name in internal
                if not (
                    name == "contracts"
                    or name.startswith("contracts.")
                    or name == "ports"
                    or name.startswith("ports.")
                    or name.startswith("knowledge_")
                )
            }
            with self.subTest(filename=path.name):
                self.assertFalse(
                    forbidden,
                    f"{path.name} imports runtime authority: {sorted(forbidden)}",
                )

    def test_implementation_and_verifier_layers_do_not_own_adjudication(self) -> None:
        forbidden = {
            "evidence_contract",
            "knowledge_graph",
            "infra.knowledge_index",
            "services.evidence_contract",
        }
        for relative in (
            "agent.py",
            "worker.py",
            "verifier.py",
            "services/verifier.py",
        ):
            path = PACKAGE_ROOT / relative
            with self.subTest(relative=relative):
                imported = _internal_imports(path)
                self.assertFalse(
                    imported.intersection(forbidden),
                    f"{relative} imports decision authority: "
                    f"{sorted(imported.intersection(forbidden))}",
                )

    def test_knowledge_index_has_no_execution_authority_dependency(self) -> None:
        paths = sorted((PACKAGE_ROOT / "infra").glob("knowledge_*.py"))
        self.assertTrue(paths, "knowledge index adapters are missing")
        forbidden = {
            "agent",
            "evolution",
            "policy",
            "provider",
            "queue",
            "runtime",
            "verifier",
            "worker",
            "services.control_outcomes",
            "services.verifier",
        }
        for path in paths:
            imported = _internal_imports(path)
            with self.subTest(filename=path.name):
                self.assertFalse(
                    imported.intersection(forbidden),
                    f"{path.name} imports execution authority: "
                    f"{sorted(imported.intersection(forbidden))}",
                )


def _internal_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module:
                imported.add(node.module)
            elif node.level == 2 and node.module:
                imported.add(node.module)
            elif node.level == 0 and node.module and node.module.startswith(
                "sisyphus_harness."
            ):
                imported.add(node.module.removeprefix("sisyphus_harness."))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("sisyphus_harness."):
                    imported.add(alias.name.removeprefix("sisyphus_harness."))
    return imported


if __name__ == "__main__":
    unittest.main()
