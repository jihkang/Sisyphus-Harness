from __future__ import annotations

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "src" / "sisyphus_harness"
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "architecture-and-data-pipeline.md"
REVIEW_DOC = REPO_ROOT / "docs" / "architecture-conformance-review-2026-07-18.md"
DOCS_INDEX = REPO_ROOT / "docs" / "README.md"


class ArchitectureDocumentationTests(unittest.TestCase):
    def test_document_matches_enforced_current_boundaries(self) -> None:
        content = ARCHITECTURE_DOC.read_text(encoding="utf-8")

        for required in (
            'CLI["cli.py"] --> Runtime["runtime.py"]',
            'Runtime --> BundleAdapter["adapters/bundle_verification.py"]',
            'Agent --> VerificationPort["VerificationPort"]',
            'Bench --> AgentFactory["AgentRunFactoryPort"]',
            "현재 구현, 전환 상태, 목표 상태",
            "VerificationServicePort",
            "atomically persisted receipt",
            "DockerVerifierTransport",
            "untrusted-contained",
            "EvidenceContract",
            "derived_candidate_only",
        ):
            with self.subTest(required=required):
                self.assertIn(required, content)

        for stale in (
            "47539e0",
            'CLI["cli.py"] --> Agent["agent.py"]',
            "일반 CLI의 기본 verification path는 아직 in-process다",
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

    def test_documentation_index_links_current_review_structure(self) -> None:
        content = DOCS_INDEX.read_text(encoding="utf-8")
        for target in (
            "architecture-and-data-pipeline.md",
            "adr/0005-default-deny-execution.md",
            "reviews/2026-07-21/README.md",
            "reviews/2026-07-21/verification-gates.md",
            "reviews/2026-07-21/stage-0-validation.md",
        ):
            with self.subTest(target=target):
                self.assertIn(target, content)
                self.assertTrue((DOCS_INDEX.parent / target).is_file())

    def test_conformance_review_pins_scope_and_open_runtime_gaps(self) -> None:
        content = REVIEW_DOC.read_text(encoding="utf-8")
        self.assertIn("origin/main@753d35531b8bf33182abc8bbc6130b124738ae36", content)
        self.assertIn("부분 일치", content)
        self.assertIn("Benchmark scoring", content)
        self.assertIn("Workspace bundle", content)
        self.assertIn("Sisyphus MCP", content)


if __name__ == "__main__":
    unittest.main()
