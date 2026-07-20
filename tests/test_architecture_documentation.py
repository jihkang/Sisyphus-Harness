from __future__ import annotations

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "src" / "sisyphus_harness"
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "architecture-and-data-pipeline.md"
REVIEW_DOC = REPO_ROOT / "docs" / "architecture-conformance-review-2026-07-18.md"


class ArchitectureDocumentationTests(unittest.TestCase):
    def test_document_matches_enforced_current_boundaries(self) -> None:
        content = ARCHITECTURE_DOC.read_text(encoding="utf-8")

        for required in (
            'CLI["cli.py"] --> Adapter["adapters/in_process.py"]',
            'Agent --> VerificationPort["VerificationPort"]',
            'Bench --> AgentFactory["AgentRunFactoryPort"]',
            "현재 구현, 전환 상태, 목표 상태",
            "VerificationServicePort",
            "atomically persisted receipt",
            "DockerVerifierTransport",
            "EvidenceContract",
            "derived_candidate_only",
        ):
            with self.subTest(required=required):
                self.assertIn(required, content)

        for stale in (
            "47539e0",
            'CLI["cli.py"] --> Agent["agent.py"]',
            "Verifier --> Models",
            "immutable receipt",
        ):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, content)

    def test_documented_python_paths_exist(self) -> None:
        content = ARCHITECTURE_DOC.read_text(encoding="utf-8")
        references = set(
            re.findall(r"`((?:[a-z_]+/)*[a-z_]+\.py)`", content)
        )
        self.assertTrue(references)
        for reference in sorted(references):
            path = (
                REPO_ROOT / reference
                if reference.startswith("tests/")
                else PACKAGE_ROOT / reference
            )
            with self.subTest(reference=reference):
                self.assertTrue(path.is_file(), f"documented code path is missing: {path}")

    def test_conformance_review_pins_scope_and_open_runtime_gaps(self) -> None:
        content = REVIEW_DOC.read_text(encoding="utf-8")
        self.assertIn("origin/main@753d35531b8bf33182abc8bbc6130b124738ae36", content)
        self.assertIn("부분 일치", content)
        self.assertIn("Benchmark scoring", content)
        self.assertIn("Workspace bundle", content)
        self.assertIn("Sisyphus MCP", content)


if __name__ == "__main__":
    unittest.main()
