from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from sisyphus_harness.adapters.receipt_observations import (
    COMMAND_PASSED,
    COMMAND_WORKSPACE_UNCHANGED,
    RECEIPT_OBSERVATION_ADAPTER_DIGEST,
    command_fact_selector,
)
from sisyphus_harness.adapters.docker_verifier import DockerVerifierTransport
from sisyphus_harness.authority import (
    authority_database_path,
    workspace_bundle_root,
)
from sisyphus_harness.config import (
    AgentLimits,
    EvolutionSettings,
    ExecutionSettings,
    HarnessConfig,
    ProviderSettings,
    VerificationConfig,
)
from sisyphus_harness.contracts.agent import AgentResult
from sisyphus_harness.contracts.control import (
    AttemptFinished,
    TaskOutcomeDecision,
)
from sisyphus_harness.contracts.evidence_contract import (
    AllOf,
    ClauseRef,
    EvidenceClause,
    EvidenceContract,
    PredicateOperator,
)
from sisyphus_harness.contracts.policy import CadencePolicy
from sisyphus_harness.contracts.verification import CommandSpec
from sisyphus_harness.contracts.verification_service import (
    BundleVerificationRequest,
    VerificationProfile,
)
from sisyphus_harness.infra.workspace_bundle import FilesystemWorkspaceBundleStore
from sisyphus_harness.infra.control_outcomes import SQLiteTaskOutcomeAuthority
from sisyphus_harness.ports.control_outcomes import TaskOutcomeRequest
from sisyphus_harness.queue import JobQueue
from sisyphus_harness.runtime import build_control_task_outcome_service

from .helpers import create_git_repo, run_git


_DOCKER_INTEGRATION = os.environ.get("SISYPHUS_DOCKER_INTEGRATION") == "1"


@unittest.skipUnless(
    _DOCKER_INTEGRATION,
    "set SISYPHUS_DOCKER_INTEGRATION=1 after building the verifier image",
)
class DockerVerifierIntegrationTests(unittest.TestCase):
    def test_real_container_enforces_runtime_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = create_git_repo(root / "repository")
            (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            run_git(repository, "add", "module.py")
            run_git(repository, "commit", "-q", "-m", "fixture")
            external_probe = root / "host-escape-probe"
            bundle_store = FilesystemWorkspaceBundleStore(root / "bundles")
            bundle = bundle_store.create(repository)
            profile = VerificationProfile(
                profile_id="container-boundary-probe",
                commands=(
                    CommandSpec(
                        name="boundary",
                        argv=(
                            "python",
                            "-c",
                            _boundary_probe(external_probe),
                        ),
                        timeout_seconds=10,
                        criteria=("container boundary holds",),
                    ),
                ),
            )
            request = BundleVerificationRequest(
                run_id="container-boundary-probe",
                workspace_bundle=bundle,
                profile=profile,
            )
            transport = DockerVerifierTransport(
                bundle_store=bundle_store.root,
                artifact_root=root / "verification",
                image=os.environ.get(
                    "SISYPHUS_VERIFIER_IMAGE",
                    "sisyphus-harness-verifier:local",
                ),
                timeout_seconds=30,
            )

            result = transport.execute(request)

            self.assertTrue(result.receipt.passed)
            self.assertTrue(result.receipt.workspace_unchanged)
            self.assertFalse(external_probe.exists())
            self.assertEqual(result.request_digest, request.request_digest)
            self.assertEqual(result.workspace_bundle_id, bundle.bundle_id)
            self.assertEqual(result.profile_digest, profile.profile_digest)
            self.assertEqual(transport.read_receipt(result.receipt_artifact), result.receipt)

    def test_real_control_path_publishes_only_docker_verified_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = create_git_repo(root / "repository")
            module = repository / "module.py"
            module.write_text("VALUE = 1\n", encoding="utf-8")
            run_git(repository, "add", "module.py")
            run_git(repository, "commit", "-q", "-m", "fixture")
            bundle_store = FilesystemWorkspaceBundleStore(
                workspace_bundle_root(repository)
            )
            source_bundle = bundle_store.create(repository)
            module.write_text("VALUE = 2\n", encoding="utf-8")
            output_bundle = bundle_store.create(repository)
            queue = JobQueue(authority_database_path(repository))
            queued = queue.enqueue(
                kind="coding-agent",
                payload={"task": "set VALUE to 2"},
                idempotency_key="container-control-task",
            )
            claimed = queue.claim(
                worker_id="container-worker",
                lease_seconds=30,
                now=100,
            )
            assert claimed is not None
            attempt = AttemptFinished(
                job_id=queued.job_id,
                attempt=1,
                attempt_id=f"{queued.job_id}/attempt-0001",
                source_bundle=source_bundle,
                output_bundle=output_bundle,
                agent_result=AgentResult(
                    run_id="container-agent",
                    success=False,
                    reason="diagnostic false must not control outcome",
                    steps=1,
                    compactions=0,
                    verifications=0,
                    workspace_state_before=source_bundle.source_state_hash,
                    workspace_state_after=output_bundle.source_state_hash,
                    changed_paths=("module.py",),
                    artifact_path="agent/container-agent",
                ),
            )
            queue.finish_attempt(
                queued.job_id,
                worker_id="container-worker",
                attempt=attempt,
                now=101,
            )
            command = CommandSpec(
                name="outcome-check",
                argv=(
                    "python",
                    "-c",
                    "import module; assert module.VALUE == 2",
                ),
                timeout_seconds=10,
                criteria=("output bundle sets VALUE to 2",),
            )
            profile = VerificationProfile(
                profile_id="control-container-profile",
                commands=(command,),
            )
            producer = "control.container.verifier"
            clauses = tuple(
                EvidenceClause(
                    clause_id=clause_id,
                    selector=command_fact_selector(
                        "outcome-check",
                        fact,
                        producer_authority=producer,
                    ),
                    operator=PredicateOperator.EQUALS,
                    expected=True,
                )
                for clause_id, fact in (
                    ("command-passed", COMMAND_PASSED),
                    ("workspace-unchanged", COMMAND_WORKSPACE_UNCHANGED),
                )
            )
            contract = EvidenceContract(
                contract_id="control-container-contract",
                version=1,
                requirement_ids=("requirement-container",),
                gap_ids=("gap-container",),
                task_basis_ids=("basis-container",),
                verification_profile_digest=profile.profile_digest,
                observation_adapter_digest=RECEIPT_OBSERVATION_ADAPTER_DIGEST,
                clauses=clauses,
                task_success=AllOf(
                    tuple(ClauseRef(clause.clause_id) for clause in clauses)
                ),
            )
            image = os.environ.get(
                "SISYPHUS_VERIFIER_IMAGE",
                "sisyphus-harness-verifier:local",
            )
            config = HarnessConfig(
                provider=ProviderSettings("http://127.0.0.1:1/v1", "unused"),
                limits=AgentLimits(),
                cadence=CadencePolicy(),
                strategy_prompt="unused",
                verification=VerificationConfig(
                    commands={"outcome-check": command},
                    selected_names=("outcome-check",),
                ),
                evolution=EvolutionSettings(),
                execution=ExecutionSettings(
                    trust_mode="trusted-in-process",
                    verifier_image=image,
                ),
            )

            outcome = build_control_task_outcome_service(
                repository,
                config,
            ).adjudicate(
                TaskOutcomeRequest(
                    job_id=queued.job_id,
                    profile=profile,
                    contract=contract,
                    run_id="control-container-final",
                    producer_authority=producer,
                )
            )

            self.assertEqual(outcome.decision, TaskOutcomeDecision.PASSED)
            self.assertFalse(attempt.agent_result.success)
            self.assertEqual(outcome.attempt_digest, attempt.attempt_digest)
            self.assertEqual(
                SQLiteTaskOutcomeAuthority(
                    authority_database_path(repository)
                ).get_task_outcome(queued.job_id),
                outcome,
            )


def _boundary_probe(external_probe: Path) -> str:
    return f"""import os, socket
assert os.getuid() != 0
status = open('/proc/self/status', encoding='utf-8').read()
assert 'CapEff:\\t0000000000000000' in status
assert 'NoNewPrivs:\\t1' in status
root_mount = next(line for line in open('/proc/mounts', encoding='utf-8') if line.split()[1] == '/')
assert 'ro' in root_mount.split()[3].split(',')
assert len(os.listdir('/bundles')) == 2
for path in ('/rootfs-write-probe', {str(external_probe)!r}):
    try:
        open(path, 'w', encoding='utf-8').write('escape')
    except OSError:
        pass
    else:
        raise AssertionError(f'container wrote outside staging: {{path}}')
sock = socket.socket()
sock.settimeout(0.2)
try:
    sock.connect(('1.1.1.1', 53))
except OSError:
    pass
else:
    raise AssertionError('external network is reachable')
assert __import__('module').VALUE == 1
"""


if __name__ == "__main__":
    unittest.main()
