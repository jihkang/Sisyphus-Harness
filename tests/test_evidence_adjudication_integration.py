from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

from sisyphus_harness.adapters.receipt_observations import (
    COMMAND_PASSED,
    COMMAND_WORKSPACE_UNCHANGED,
    RECEIPT_OBSERVATION_ADAPTER_DIGEST,
    command_fact_selector,
)
from sisyphus_harness.contracts.agent import AgentResult
from sisyphus_harness.contracts.control import AttemptFinished
from sisyphus_harness.contracts.evidence_contract import (
    AllOf,
    ClauseRef,
    EvidenceClause,
    EvidenceContract,
    LogicalResult,
    PredicateOperator,
)
from sisyphus_harness.contracts.verification import CommandSpec
from sisyphus_harness.contracts.verification_service import VerificationProfile
from sisyphus_harness.infra.workspace_bundle import FilesystemWorkspaceBundleStore
from sisyphus_harness.ports.evidence_contracts import EvidenceAdjudicationRequest
from sisyphus_harness.services.evidence_contract import ControlEvidenceContractService
from sisyphus_harness.services.verifier import BundleVerifierService

from .helpers import create_git_repo


class EvidenceAdjudicationIntegrationTests(unittest.TestCase):
    def test_real_verifier_materializes_exact_output_bundle_before_control_evaluation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = create_git_repo(root / "repository")
            bundle_store = FilesystemWorkspaceBundleStore(root / "bundles")
            source_bundle = bundle_store.create(repository)
            (repository / "tracked.txt").write_text("implemented\n", encoding="utf-8")
            output_bundle = bundle_store.create(repository)
            self.assertNotEqual(source_bundle.bundle_id, output_bundle.bundle_id)

            agent_result = AgentResult(
                run_id="agent-integration",
                success=False,
                reason="implementation agent did not claim completion",
                steps=1,
                compactions=0,
                verifications=0,
                workspace_state_before=source_bundle.source_state_hash,
                workspace_state_after=output_bundle.source_state_hash,
                changed_paths=("tracked.txt",),
                artifact_path="agent/agent-integration",
            )
            job_result = AttemptFinished(
                job_id="integration-job",
                attempt=1,
                attempt_id="integration-job/attempt-0001",
                source_bundle=source_bundle,
                output_bundle=output_bundle,
                agent_result=agent_result,
            )
            profile = VerificationProfile(
                profile_id="integration-profile",
                commands=(
                    CommandSpec(
                        name="output-check",
                        argv=(
                            sys.executable,
                            "-c",
                            (
                                "from pathlib import Path; "
                                "assert Path('tracked.txt').read_text() "
                                "== 'implemented\\n'"
                            ),
                        ),
                        timeout_seconds=5,
                        criteria=("legacy compatibility criterion",),
                    ),
                ),
            )
            authority = "control.integration.verifier"
            clauses = (
                EvidenceClause(
                    clause_id="command-passed",
                    selector=command_fact_selector(
                        "output-check",
                        COMMAND_PASSED,
                        producer_authority=authority,
                    ),
                    operator=PredicateOperator.EQUALS,
                    expected=True,
                ),
                EvidenceClause(
                    clause_id="workspace-unchanged",
                    selector=command_fact_selector(
                        "output-check",
                        COMMAND_WORKSPACE_UNCHANGED,
                        producer_authority=authority,
                    ),
                    operator=PredicateOperator.EQUALS,
                    expected=True,
                ),
            )
            contract = EvidenceContract(
                contract_id="integration-contract",
                version=1,
                requirement_ids=("requirement-integration",),
                gap_ids=("gap-integration",),
                task_basis_ids=("basis-integration",),
                verification_profile_digest=profile.profile_digest,
                observation_adapter_digest=RECEIPT_OBSERVATION_ADAPTER_DIGEST,
                clauses=clauses,
                task_success=AllOf(
                    tuple(ClauseRef(clause.clause_id) for clause in clauses)
                ),
            )

            service = ControlEvidenceContractService(
                BundleVerifierService(
                    bundle_store=bundle_store,
                    artifact_root=root / "verification-artifacts",
                    work_root=root / "verification-work",
                )
            )
            result = service.adjudicate(
                EvidenceAdjudicationRequest(
                    job_result=job_result,
                    profile=profile,
                    contract=contract,
                    run_id="integration-final",
                    producer_authority=authority,
                )
            )

            self.assertFalse(result.agent_reported_success)
            self.assertEqual(result.output_bundle_id, output_bundle.bundle_id)
            self.assertEqual(
                result.verification_request.workspace_bundle,
                output_bundle,
            )
            self.assertTrue(result.verification_result.receipt.passed)
            self.assertEqual(result.evaluation.logical_result, LogicalResult.PASS)
            self.assertEqual(list((root / "verification-work").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
