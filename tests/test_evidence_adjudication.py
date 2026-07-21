from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
import unittest

from sisyphus_harness.adapters.receipt_observations import (
    COMMAND_EXIT_CODE,
    COMMAND_FAILURE_CATEGORY,
    COMMAND_PASSED,
    COMMAND_TIMED_OUT,
    COMMAND_WORKSPACE_UNCHANGED,
    NO_FAILURE_CATEGORY,
    RECEIPT_OBSERVATION_ADAPTER_DIGEST,
    VERIFICATION_RECEIPT_MEDIA_TYPE,
    ReceiptObservationAdapter,
    VerificationBindingError,
    command_fact_selector,
)
from sisyphus_harness.contracts.agent import AgentResult
from sisyphus_harness.contracts.artifacts import ArtifactRef
from sisyphus_harness.contracts.control import AttemptFinished
from sisyphus_harness.contracts.evidence_contract import (
    AllOf,
    ClauseRef,
    EvidenceClause,
    EvidenceContract,
    EvidenceObservation,
    EvaluationLifecycle,
    LogicalResult,
    ObservationStatus,
    PredicateOperator,
)
from sisyphus_harness.contracts.verification import (
    CommandResult,
    CommandSpec,
    VerificationReceipt,
)
from sisyphus_harness.contracts.verification_service import (
    BundleVerificationRequest,
    VerificationProfile,
    VerificationServiceResult,
)
from sisyphus_harness.contracts.workspace import WorkspaceBundleRef
from sisyphus_harness.ports.evidence_contracts import (
    EvidenceAdjudicationRequest,
    EvidenceContractAdjudicationPort,
    ReceiptObservationPort,
)
from sisyphus_harness.services.evidence_contract import ControlEvidenceContractService


_AUTHORITY = "control.verifier.local"
_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_DIGEST_C = "sha256:" + "c" * 64


def _bundle(character: str) -> WorkspaceBundleRef:
    digest = "sha256:" + character * 64
    return WorkspaceBundleRef(
        bundle_id=f"workspace:{digest}",
        archive_sha256=digest,
        size_bytes=100,
        source_commit_sha=character * 40,
        source_state_hash="sha256:" + character * 64,
        tree_hash="sha256:" + character * 64,
        changed_paths=("module.py",),
        entry_count=1,
    )


def _job(*, agent_success: bool) -> AttemptFinished:
    agent = AgentResult(
        run_id="agent-run-1",
        success=agent_success,
        reason="agent stopped",
        steps=1,
        compactions=0,
        verifications=0,
        workspace_state_before="before",
        workspace_state_after="after",
        changed_paths=("module.py",),
        artifact_path="agent/agent-run-1",
    )
    return AttemptFinished(
        job_id="job-1",
        attempt=1,
        attempt_id="job-1/attempt-0001",
        source_bundle=_bundle("a"),
        output_bundle=_bundle("b"),
        agent_result=agent,
    )


def _profile() -> VerificationProfile:
    return VerificationProfile(
        profile_id="operator-profile-1",
        commands=(
            CommandSpec(
                name="unit-tests",
                argv=("python", "-m", "unittest"),
                timeout_seconds=30,
                criteria=("semantic criterion must not become evidence",),
            ),
        ),
    )


def _contract(profile: VerificationProfile | None = None) -> EvidenceContract:
    profile = profile or _profile()
    clauses = (
        EvidenceClause(
            clause_id="command-passed",
            selector=command_fact_selector(
                "unit-tests",
                COMMAND_PASSED,
                producer_authority=_AUTHORITY,
            ),
            operator=PredicateOperator.EQUALS,
            expected=True,
        ),
        EvidenceClause(
            clause_id="workspace-stable",
            selector=command_fact_selector(
                "unit-tests",
                COMMAND_WORKSPACE_UNCHANGED,
                producer_authority=_AUTHORITY,
            ),
            operator=PredicateOperator.EQUALS,
            expected=True,
        ),
    )
    return EvidenceContract(
        contract_id="task-contract-1",
        version=1,
        requirement_ids=("requirement-1",),
        gap_ids=("gap-1",),
        task_basis_ids=("basis-1",),
        verification_profile_digest=profile.profile_digest,
        observation_adapter_digest=RECEIPT_OBSERVATION_ADAPTER_DIGEST,
        clauses=clauses,
        task_success=AllOf(tuple(ClauseRef(item.clause_id) for item in clauses)),
    )


class ResultVerifier:
    def __init__(self, *, command_passed: bool, mutation=None) -> None:
        self.command_passed = command_passed
        self.mutation = mutation
        self.requests: list[BundleVerificationRequest] = []
        self.latest_result: VerificationServiceResult | None = None

    def execute(self, request: BundleVerificationRequest) -> VerificationServiceResult:
        self.requests.append(request)
        failure_category = None if self.command_passed else "assertion_failure"
        bound_request_digest = (
            _DIGEST_C if self.mutation == "request" else request.request_digest
        )
        command = CommandResult(
            name=(
                "different-command"
                if self.mutation == "command"
                else request.profile.commands[0].name
            ),
            argv=request.profile.commands[0].argv,
            criteria=request.profile.commands[0].criteria,
            passed=self.command_passed,
            timed_out=False,
            exit_code=0 if self.command_passed else 1,
            duration_ms=10,
            executable_path="/usr/bin/python",
            executable_sha256=_DIGEST_A,
            stdout_path="00-unit-tests/stdout.txt",
            stderr_path="00-unit-tests/stderr.txt",
            workspace_state_before=request.workspace_bundle.tree_hash,
            workspace_state_after=request.workspace_bundle.tree_hash,
            workspace_unchanged=True,
            failure_category=failure_category,
            error=None,
        )
        receipt_run_id = (
            "another-final-run"
            if self.mutation == "run"
            else request.run_id
        )
        receipt = VerificationReceipt(
            run_id=receipt_run_id,
            workspace="/materialized/output",
            worktree_commit_sha=(
                "c" * 40
                if self.mutation == "commit"
                else request.workspace_bundle.source_commit_sha
            ),
            started_at="2026-07-20T00:00:00Z",
            finished_at="2026-07-20T00:00:01Z",
            passed=self.command_passed,
            commands=(command,),
            workspace_state_before=request.workspace_bundle.tree_hash,
            workspace_state_after=request.workspace_bundle.tree_hash,
            workspace_unchanged=True,
            request_digest=bound_request_digest,
        )
        if self.mutation == "tree":
            object.__setattr__(receipt, "workspace_state_before", _DIGEST_C)
        result = VerificationServiceResult(
            request_digest=bound_request_digest,
            workspace_bundle_id=(
                "workspace:wrong"
                if self.mutation == "bundle"
                else request.workspace_bundle.bundle_id
            ),
            profile_digest=(
                _DIGEST_C
                if self.mutation == "profile"
                else request.profile.profile_digest
            ),
            receipt=receipt,
            receipt_artifact=ArtifactRef(
                artifact_id=f"{receipt_run_id}/receipt.json",
                sha256=_DIGEST_B,
                size_bytes=100,
                media_type=(
                    "application/json"
                    if self.mutation == "media"
                    else VERIFICATION_RECEIPT_MEDIA_TYPE
                ),
            ),
        )
        self.latest_result = result
        return result

    def read_receipt(self, reference: ArtifactRef) -> VerificationReceipt:
        if self.latest_result is None:
            raise ValueError("verification has not produced a receipt")
        if reference != self.latest_result.receipt_artifact:
            raise ValueError("receipt reference does not match the latest result")
        return self.latest_result.receipt


class ForeignObservationAdapter:
    adapter_digest = RECEIPT_OBSERVATION_ADAPTER_DIGEST

    def adapt(self, *, request, result, producer_authority):
        observations = ReceiptObservationAdapter().adapt(
            request=request,
            result=result,
            producer_authority=producer_authority,
        )
        foreign = replace(observations[0], subject_digest=_DIGEST_C)
        return (foreign, *observations[1:])


class StatefulObservationTuple(tuple):
    """Models an adapter result that changes across successive iterations."""

    def __new__(cls, first, second):
        instance = super().__new__(cls, first)
        instance.first = first
        instance.second = second
        instance.iterations = 0
        return instance

    def __iter__(self):
        self.iterations += 1
        return iter(self.first if self.iterations == 1 else self.second)


class StatefulObservationAdapter:
    adapter_digest = RECEIPT_OBSERVATION_ADAPTER_DIGEST

    def __init__(self) -> None:
        self.output = None

    def adapt(self, *, request, result, producer_authority):
        observations = ReceiptObservationAdapter().adapt(
            request=request,
            result=result,
            producer_authority=producer_authority,
        )
        forged = (
            replace(observations[0], value=False),
            *observations[1:],
        )
        self.output = StatefulObservationTuple(observations, forged)
        return self.output


class StatefulEvidenceObservation(EvidenceObservation):
    """Changes a validated command fact if subclasses cross the boundary."""

    def __init__(self, **kwargs) -> None:
        object.__setattr__(self, "_value_reads", 0)
        super().__init__(**kwargs)

    def __getattribute__(self, name):
        if name == "value":
            reads = object.__getattribute__(self, "_value_reads") + 1
            object.__setattr__(self, "_value_reads", reads)
            value = super().__getattribute__(name)
            return value if reads <= 4 else True
        return super().__getattribute__(name)


class StatefulObservationValueAdapter:
    adapter_digest = RECEIPT_OBSERVATION_ADAPTER_DIGEST

    def adapt(self, *, request, result, producer_authority):
        observations = ReceiptObservationAdapter().adapt(
            request=request,
            result=result,
            producer_authority=producer_authority,
        )
        original = observations[0]
        changing = StatefulEvidenceObservation(
            observation_id=original.observation_id,
            selector=original.selector,
            subject_digest=original.subject_digest,
            source_run_id=original.source_run_id,
            artifact_digest=original.artifact_digest,
            status=original.status,
            value=original.value,
            reason_code=original.reason_code,
            schema_version=original.schema_version,
        )
        return (changing, *observations[1:])


class AttemptFinishedSubclass(AttemptFinished):
    pass


class AgentResultSubclass(AgentResult):
    pass


class WorkspaceBundleRefSubclass(WorkspaceBundleRef):
    pass


class VerificationProfileSubclass(VerificationProfile):
    pass


class CommandSpecSubclass(CommandSpec):
    pass


class EvidenceContractSubclass(EvidenceContract):
    pass


class EvidenceAdjudicationRequestSubclass(EvidenceAdjudicationRequest):
    pass


class StaticResultVerifier:
    def __init__(self, transform) -> None:
        self.transform = transform
        self.latest_result: VerificationServiceResult | None = None

    def execute(self, request: BundleVerificationRequest) -> VerificationServiceResult:
        result = ResultVerifier(command_passed=True).execute(request)
        self.latest_result = self.transform(result)
        return self.latest_result

    def read_receipt(self, reference: ArtifactRef) -> VerificationReceipt:
        if self.latest_result is None:
            raise ValueError("verification has not produced a receipt")
        if reference != self.latest_result.receipt_artifact:
            raise ValueError("receipt reference does not match the latest result")
        return self.latest_result.receipt


def _request(job: AttemptFinished) -> EvidenceAdjudicationRequest:
    profile = _profile()
    return EvidenceAdjudicationRequest(
        job_result=job,
        profile=profile,
        contract=_contract(profile),
        run_id="final-job-1-attempt-0001",
        producer_authority=_AUTHORITY,
    )


class EvidenceAdjudicationTests(unittest.TestCase):
    def test_agent_false_does_not_block_a_passing_observed_contract(self) -> None:
        verifier = ResultVerifier(command_passed=True)
        service = ControlEvidenceContractService(verifier)
        request = _request(_job(agent_success=False))

        result = service.adjudicate(request)

        self.assertEqual(result.evaluation.lifecycle, EvaluationLifecycle.COMPLETED)
        self.assertEqual(result.evaluation.logical_result, LogicalResult.PASS)
        self.assertFalse(result.agent_reported_success)
        self.assertIs(
            verifier.requests[0].workspace_bundle,
            request.job_result.output_bundle,
        )
        self.assertEqual(
            result.output_bundle_id,
            request.job_result.output_bundle.bundle_id,
        )
        self.assertIsInstance(service, EvidenceContractAdjudicationPort)

    def test_agent_true_cannot_override_failing_evidence(self) -> None:
        result = ControlEvidenceContractService(
            ResultVerifier(command_passed=False)
        ).adjudicate(_request(_job(agent_success=True)))

        self.assertTrue(result.agent_reported_success)
        self.assertEqual(result.evaluation.logical_result, LogicalResult.FAIL)
        self.assertEqual(
            result.evaluation.predicates[0].reason_code,
            "predicate_not_satisfied",
        )

    def test_adapter_emits_only_five_typed_command_facts(self) -> None:
        verifier = ResultVerifier(command_passed=True)
        adjudication_request = _request(_job(agent_success=True))
        bundle_request = BundleVerificationRequest(
            run_id=adjudication_request.run_id,
            workspace_bundle=adjudication_request.job_result.output_bundle,
            profile=adjudication_request.profile,
        )
        service_result = verifier.execute(bundle_request)
        adapter = ReceiptObservationAdapter()

        observations = adapter.adapt(
            request=bundle_request,
            result=service_result,
            producer_authority=_AUTHORITY,
        )

        self.assertIsInstance(adapter, ReceiptObservationPort)
        self.assertEqual(
            tuple(item.selector.observation_type for item in observations),
            (
                COMMAND_PASSED,
                COMMAND_EXIT_CODE,
                COMMAND_TIMED_OUT,
                COMMAND_WORKSPACE_UNCHANGED,
                COMMAND_FAILURE_CATEGORY,
            ),
        )
        self.assertTrue(
            all(
                item.subject_digest
                == bundle_request.workspace_bundle.archive_sha256
                for item in observations
            )
        )
        self.assertTrue(
            all(
                item.artifact_digest == service_result.receipt_artifact.sha256
                for item in observations
            )
        )
        self.assertTrue(
            all(item.status is ObservationStatus.OBSERVED for item in observations)
        )
        self.assertEqual(observations[-1].value, NO_FAILURE_CATEGORY)
        encoded = json.dumps([item.to_dict() for item in observations])
        self.assertNotIn("semantic criterion must not become evidence", encoded)
        self.assertNotIn("stdout", encoded)
        self.assertNotIn("stderr", encoded)

    def test_control_rejects_every_cross_boundary_identity_mismatch(self) -> None:
        for mutation, message in (
            ("request", "different request"),
            ("bundle", "workspace bundle"),
            ("profile", "different profile"),
            ("run", "different run"),
            ("commit", "source commit"),
            ("tree", "workspace tree"),
            ("media", "media type"),
            ("command", "does not match the profile"),
        ):
            with self.subTest(mutation=mutation):
                service = ControlEvidenceContractService(
                    ResultVerifier(command_passed=True, mutation=mutation)
                )
                with self.assertRaisesRegex(VerificationBindingError, message):
                    service.adjudicate(_request(_job(agent_success=True)))

    def test_adjudication_result_rejects_internal_binding_mismatches(self) -> None:
        valid = ControlEvidenceContractService(
            ResultVerifier(command_passed=True)
        ).adjudicate(_request(_job(agent_success=True)))

        def wrong_request_digest():
            result = replace(valid.verification_result)
            object.__setattr__(result, "request_digest", _DIGEST_C)
            return replace(valid, verification_result=result)

        def wrong_receipt_run():
            receipt = replace(
                valid.verification_result.receipt,
                run_id="different-run",
            )
            artifact = replace(
                valid.verification_result.receipt_artifact,
                artifact_id="different-run/receipt.json",
            )
            result = replace(
                valid.verification_result,
                receipt=receipt,
                receipt_artifact=artifact,
            )
            return replace(valid, verification_result=result)

        mutations = (
            (
                lambda: replace(valid, output_bundle_id=_bundle("c").bundle_id),
                "output bundle",
            ),
            (wrong_request_digest, "does not match its request"),
            (
                lambda: replace(
                    valid,
                    verification_result=replace(
                        valid.verification_result,
                        workspace_bundle_id=_bundle("c").bundle_id,
                    ),
                ),
                "does not match the output bundle",
            ),
            (
                lambda: replace(
                    valid,
                    verification_result=replace(
                        valid.verification_result,
                        profile_digest=_DIGEST_C,
                    ),
                ),
                "does not match the profile",
            ),
            (wrong_receipt_run, "does not match the verification run"),
        )
        for mutation, message in mutations:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    mutation()

    def test_control_rejects_foreign_facts_from_a_custom_adapter(self) -> None:
        service = ControlEvidenceContractService(
            ResultVerifier(command_passed=True),
            ForeignObservationAdapter(),
        )

        with self.assertRaisesRegex(
            VerificationBindingError,
            "different workspace bundle",
        ):
            service.adjudicate(_request(_job(agent_success=True)))

    def test_control_snapshots_stateful_adapter_output_once(self) -> None:
        adapter = StatefulObservationAdapter()
        result = ControlEvidenceContractService(
            ResultVerifier(command_passed=True),
            adapter,
        ).adjudicate(_request(_job(agent_success=True)))

        self.assertEqual(result.evaluation.logical_result, LogicalResult.PASS)
        self.assertEqual(adapter.output.iterations, 1)
        self.assertIs(type(result.observations), tuple)

    def test_control_rejects_stateful_evidence_observation_subclasses(self) -> None:
        with self.assertRaisesRegex(TypeError, "exact EvidenceObservation"):
            ControlEvidenceContractService(
                ResultVerifier(command_passed=False),
                StatefulObservationValueAdapter(),
            ).adjudicate(_request(_job(agent_success=False)))

    def test_adjudication_authority_rejects_request_model_subclasses(self) -> None:
        request = _request(_job(agent_success=True))
        substitutions = (
            (
                "job_result",
                AttemptFinishedSubclass.from_dict(request.job_result.to_dict()),
                "job result",
            ),
            (
                "profile",
                VerificationProfileSubclass.from_dict(request.profile.to_dict()),
                "profile",
            ),
            (
                "contract",
                EvidenceContractSubclass.from_dict(request.contract.to_dict()),
                "contract",
            ),
        )
        for field_name, value, message in substitutions:
            with self.subTest(field=field_name):
                with self.assertRaisesRegex(TypeError, message):
                    replace(request, **{field_name: value})

        subclass = EvidenceAdjudicationRequestSubclass(
            job_result=request.job_result,
            profile=request.profile,
            contract=request.contract,
            run_id=request.run_id,
            producer_authority=request.producer_authority,
        )
        with self.assertRaisesRegex(TypeError, "exact EvidenceAdjudicationRequest"):
            ControlEvidenceContractService(
                ResultVerifier(command_passed=True)
            ).adjudicate(subclass)

    def test_coding_job_and_profile_reject_nested_model_subclasses(self) -> None:
        job = _job(agent_success=True)
        bundle_subclass = WorkspaceBundleRefSubclass.from_dict(
            job.output_bundle.to_dict()
        )
        nested_job_values = (
            ("source_bundle", bundle_subclass, "source bundle"),
            ("output_bundle", bundle_subclass, "output bundle"),
            (
                "agent_result",
                AgentResultSubclass.from_dict(job.agent_result.to_dict()),
                "agent result",
            ),
        )
        for field_name, value, message in nested_job_values:
            with self.subTest(field=field_name):
                with self.assertRaisesRegex(TypeError, message):
                    replace(job, **{field_name: value})

        profile = _profile()
        command_subclass = CommandSpecSubclass.from_dict(
            profile.commands[0].to_dict()
        )
        with self.assertRaisesRegex(ValueError, "exact CommandSpec"):
            replace(profile, commands=(command_subclass,))

    def test_workspace_state_claims_and_boundaries_are_fail_closed(self) -> None:
        def inconsistent_receipt(result):
            receipt = replace(result.receipt)
            object.__setattr__(receipt, "workspace_state_after", _DIGEST_C)
            return replace(result, receipt=receipt)

        def inconsistent_command(result):
            command = result.receipt.commands[0]
            object.__setattr__(command, "workspace_state_after", _DIGEST_C)
            return result

        def broken_boundary(result):
            command = replace(
                result.receipt.commands[0],
                workspace_state_before=_DIGEST_C,
                workspace_state_after=_DIGEST_C,
            )
            receipt = replace(result.receipt, commands=(command,))
            return replace(result, receipt=receipt)

        for transform, message in (
            (inconsistent_receipt, "receipt workspace state claim"),
            (inconsistent_command, "command 0 workspace state claim"),
            (broken_boundary, "breaks the workspace state chain"),
        ):
            with self.subTest(message=message):
                service = ControlEvidenceContractService(
                    StaticResultVerifier(transform)
                )
                with self.assertRaisesRegex(VerificationBindingError, message):
                    service.adjudicate(_request(_job(agent_success=True)))

    def test_inconsistent_command_pass_claim_cannot_reach_the_evaluator(self) -> None:
        request = _request(_job(agent_success=True))
        bundle_request = BundleVerificationRequest(
            run_id=request.run_id,
            workspace_bundle=request.job_result.output_bundle,
            profile=request.profile,
        )
        valid = ResultVerifier(command_passed=True).execute(bundle_request)
        with self.assertRaisesRegex(ValueError, "passing.*inconsistent"):
            replace(
                valid.receipt.commands[0],
                exit_code=17,
                timed_out=True,
                failure_category="timeout",
            )

        def forged_transport_object(result):
            command = result.receipt.commands[0]
            # Frozen dataclasses prevent normal mutation. This simulates a buggy
            # in-process transport bypassing the strict wire parser entirely.
            object.__setattr__(command, "exit_code", 17)
            object.__setattr__(command, "timed_out", True)
            object.__setattr__(command, "failure_category", "timeout")
            return result

        with self.assertRaisesRegex(
            VerificationBindingError,
            "command 0 pass claim is inconsistent",
        ):
            ControlEvidenceContractService(
                StaticResultVerifier(forged_transport_object)
            ).adjudicate(request)

    def test_consistent_mutating_receipt_remains_evidence_not_transport_error(self) -> None:
        def workspace_mutation(result):
            command = replace(
                result.receipt.commands[0],
                passed=False,
                exit_code=1,
                workspace_state_after=_DIGEST_C,
                workspace_unchanged=False,
                failure_category="workspace_mutation",
            )
            receipt = replace(
                result.receipt,
                passed=False,
                commands=(command,),
                workspace_state_after=_DIGEST_C,
                workspace_unchanged=False,
            )
            return replace(result, receipt=receipt)

        result = ControlEvidenceContractService(
            StaticResultVerifier(workspace_mutation)
        ).adjudicate(_request(_job(agent_success=True)))

        self.assertEqual(result.evaluation.lifecycle, EvaluationLifecycle.COMPLETED)
        self.assertEqual(result.evaluation.logical_result, LogicalResult.FAIL)
        workspace_observation = next(
            item
            for item in result.observations
            if item.selector.observation_type == COMMAND_WORKSPACE_UNCHANGED
        )
        self.assertIsInstance(workspace_observation, EvidenceObservation)
        self.assertFalse(workspace_observation.value)

    def test_runtime_request_and_result_are_immutable(self) -> None:
        request = _request(_job(agent_success=False))
        result = ControlEvidenceContractService(
            ResultVerifier(command_passed=True)
        ).adjudicate(request)

        with self.assertRaises(FrozenInstanceError):
            request.run_id = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            result.agent_reported_success = True  # type: ignore[misc]
        self.assertFalse(hasattr(result, "complete_task"))
        self.assertFalse(hasattr(result, "update_queue"))


if __name__ == "__main__":
    unittest.main()
