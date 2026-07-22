from __future__ import annotations

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "src" / "sisyphus_harness"
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "architecture-and-data-pipeline.md"
ARCHITECTURE_INDEX = REPO_ROOT / "docs" / "architecture" / "README.md"
REVIEW_DOC = REPO_ROOT / "docs" / "architecture-conformance-review-2026-07-18.md"
DOCS_INDEX = REPO_ROOT / "docs" / "README.md"
STATUS_INDEX = REPO_ROOT / "docs" / "status" / "README.md"
CONFORMANCE_MODEL = REPO_ROOT / "docs" / "status" / "conformance-model.md"
DEBT_REGISTER = REPO_ROOT / "docs" / "status" / "implementation-debt.md"
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


class ArchitectureDocumentationTests(unittest.TestCase):
    def test_document_matches_enforced_current_boundaries(self) -> None:
        content = ARCHITECTURE_DOC.read_text(encoding="utf-8")

        for required in (
            'CLI["cli.py"] --> Runtime["runtime.py"]',
            'Runtime --> BundleAdapter["adapters/bundle_verification.py"]',
            'Agent --> AgentLoop["agent_loop.py"]',
            'AgentTransitions --> VerificationPort["VerificationPort"]',
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
            "architecture/README.md",
            "architecture-and-data-pipeline.md",
            "adr/0005-default-deny-execution.md",
            "adr/0007-verifier-asset-and-image-binding.md",
            "adr/0008-host-owned-verification-evidence.md",
            "reviews/2026-07-21/README.md",
            "reviews/2026-07-21/verification-gates.md",
            "reviews/2026-07-21/stage-0-validation.md",
            "reviews/2026-07-21/stage-c-verifier-integrity.md",
            "status/README.md",
            "status/conformance-model.md",
            "status/implementation-debt.md",
        ):
            with self.subTest(target=target):
                self.assertIn(target, content)
                self.assertTrue((DOCS_INDEX.parent / target).is_file())

    def test_structured_architecture_map_keeps_service_authority_explicit(self) -> None:
        content = ARCHITECTURE_INDEX.read_text(encoding="utf-8")
        component_documents = {
            "Agent": "components/agent.md",
            "Verifier": "components/verifier.md",
            "Evolve": "components/evolve.md",
            "Control": "components/control.md",
        }
        for boundary, target in component_documents.items():
            with self.subTest(boundary=boundary):
                self.assertIn(target, content)
                component = (ARCHITECTURE_INDEX.parent / target).read_text(
                    encoding="utf-8"
                )
                self.assertIn("## Responsibility", component)
                self.assertIn("## Owned Authority", component)
                self.assertIn("## Forbidden Authority", component)
                self.assertIn("## Current Implementation", component)
                self.assertIn("## Target Boundary", component)
                self.assertIn("## Open Debt And Evidence", component)

        for target in ("trust-and-artifacts.md", "data-pipelines.md"):
            with self.subTest(target=target):
                self.assertIn(target, content)
                self.assertTrue((ARCHITECTURE_INDEX.parent / target).is_file())

        verifier = (
            ARCHITECTURE_INDEX.parent / "components" / "verifier.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Read-only mounting protects verifier asset integrity", verifier)
        self.assertIn("separate evaluator process or", verifier)
        self.assertIn("VerificationExecutorPort", verifier)
        self.assertIn("host constructs", verifier)

    def test_living_status_documents_define_canonical_conformance_and_debt(self) -> None:
        status_index = STATUS_INDEX.read_text(encoding="utf-8")
        conformance = CONFORMANCE_MODEL.read_text(encoding="utf-8")
        debt = DEBT_REGISTER.read_text(encoding="utf-8")

        self.assertIn("implementation-debt.md", status_index)
        self.assertIn("conformance-model.md", status_index)
        for token in ("GREEN", "AMBER", "RED", "GRAY"):
            with self.subTest(token=token):
                self.assertIn(f"`{token}`", conformance)
        for debt_id in (
            "SH-P0-002",
            "SH-VERIFY-001",
            "SH-VERIFY-002",
            "SH-ORACLE-001",
            "SH-GRAPH-001",
            "SH-ARCH-001",
            "SH-TEST-001",
            "SH-EVOLVE-001",
            "SH-BENCH-001",
            "SH-GOV-001",
        ):
            with self.subTest(debt_id=debt_id):
                self.assertEqual(debt.count(f"`{debt_id}`"), 1)

    def test_slice_b_status_is_bound_to_merged_head_evidence(self) -> None:
        findings = (
            REPO_ROOT / "docs" / "reviews" / "2026-07-21" / "findings.md"
        ).read_text(encoding="utf-8")
        record = (
            REPO_ROOT
            / "docs"
            / "reviews"
            / "2026-07-21"
            / "stage-b-control-authority.md"
        ).read_text(encoding="utf-8")

        self.assertIn("Closed by merged PR #9 at `8cccfef`", findings)
        self.assertIn("29839229786", record)
        self.assertIn("8cccfef9e6726cb64623b9ba85d35ee69d2e6b8a", record)

    def test_slice_c_status_is_bound_to_merged_head_evidence(self) -> None:
        debt = DEBT_REGISTER.read_text(encoding="utf-8")
        plan = (
            REPO_ROOT
            / "docs"
            / "plans"
            / "2026-07-22-verifier-command-isolation.md"
        ).read_text(encoding="utf-8")
        review = (
            REPO_ROOT
            / "docs"
            / "reviews"
            / "2026-07-22"
            / "verifier-command-isolation.md"
        ).read_text(encoding="utf-8")

        for marker in (
            "PR #11 at `5d872bc`",
            "29848008998",
            "SH-VERIFY-001",
            "SH-VERIFY-002",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, debt)
        self.assertNotIn("| `SH-VERIFY-001` | P0", debt)
        self.assertNotIn("| `SH-VERIFY-002` | P0", debt)
        self.assertIn("- Status: Complete", plan)
        self.assertIn("5d872bc6a064e5f5f36aa46df31813a4ca2d4608", plan)
        self.assertIn("c0650c7b9d24fde857524107f559833470176d55", review)
        self.assertIn("29848008998", review)

    def test_docker_decomposition_is_bound_to_merged_head_evidence(self) -> None:
        debt = DEBT_REGISTER.read_text(encoding="utf-8")
        plan = (
            REPO_ROOT
            / "docs"
            / "plans"
            / "2026-07-22-responsibility-decomposition.md"
        ).read_text(encoding="utf-8")
        review = (
            REPO_ROOT
            / "docs"
            / "reviews"
            / "2026-07-22"
            / "docker-verifier-decomposition.md"
        ).read_text(encoding="utf-8")

        for marker in ("PR #13", "a3a0121", "29915544947"):
            with self.subTest(marker=marker):
                self.assertIn(marker, debt)
                self.assertIn(marker, plan)
                self.assertIn(marker, review)
        self.assertIn("f7cb081e4819113fa69005de89e9cfae5862258b", review)
        self.assertIn("a3a0121eb7b245828de9cca4001da7c568f30d85", review)

    def test_relative_documentation_links_resolve(self) -> None:
        for document in sorted((REPO_ROOT / "docs").rglob("*.md")):
            content = document.read_text(encoding="utf-8")
            for raw_target in MARKDOWN_LINK.findall(content):
                target = raw_target.split("#", 1)[0]
                if (
                    not target
                    or target.startswith(("https://", "http://", "mailto:"))
                ):
                    continue
                resolved = (document.parent / target).resolve()
                with self.subTest(
                    document=document.relative_to(REPO_ROOT),
                    target=raw_target,
                ):
                    self.assertTrue(
                        resolved.exists(),
                        f"broken relative documentation link: {raw_target}",
                    )

    def test_conformance_review_pins_scope_and_open_runtime_gaps(self) -> None:
        content = REVIEW_DOC.read_text(encoding="utf-8")
        self.assertIn("origin/main@753d35531b8bf33182abc8bbc6130b124738ae36", content)
        self.assertIn("부분 일치", content)
        self.assertIn("Benchmark scoring", content)
        self.assertIn("Workspace bundle", content)
        self.assertIn("Sisyphus MCP", content)


if __name__ == "__main__":
    unittest.main()
