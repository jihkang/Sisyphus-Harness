from __future__ import annotations

import ast
from pathlib import Path
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "sisyphus_harness"


class EvidenceGraphArchitectureTests(unittest.TestCase):
    def test_pure_decision_domains_depend_only_on_contracts(self) -> None:
        for filename in ("evidence_contract.py", "knowledge_graph.py"):
            path = PACKAGE_ROOT / filename
            self.assertTrue(path.is_file(), f"missing pure domain: {filename}")
            internal = _internal_imports(path)
            with self.subTest(filename=filename):
                self.assertTrue(
                    all(name == "contracts" or name.startswith("contracts.") for name in internal),
                    f"{filename} imports non-contract runtime modules: {sorted(internal)}",
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
        path = PACKAGE_ROOT / "infra" / "knowledge_index.py"
        self.assertTrue(path.is_file(), "knowledge index adapter is missing")
        forbidden = {
            "agent",
            "queue",
            "verifier",
            "worker",
            "services.verifier",
        }
        imported = _internal_imports(path)
        self.assertFalse(
            imported.intersection(forbidden),
            f"knowledge index imports execution authority: "
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
