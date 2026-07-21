from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from sisyphus_harness.adapters.receipt_observations import (
    COMMAND_EXIT_CODE,
    COMMAND_PASSED,
    FINAL_VERIFICATION_STAGE,
    RECEIPT_OBSERVATION_ADAPTER_DIGEST,
    ReceiptObservationAdapter,
    VerificationBindingError,
    command_fact_selector,
    validate_final_command_observations,
    validate_final_verification_bindings,
)
from sisyphus_harness.contracts.evidence_contract import (
    EvidenceObservation,
    ObservationStatus,
)
from sisyphus_harness.contracts.verification_service import (
    BundleVerificationRequest,
    VerificationServiceResult,
)
from sisyphus_harness.infra.verification_evidence import (
    FilesystemVerificationEvidenceStore,
    VerificationEvidenceError,
)
from sisyphus_harness.receipts import write_json_atomic
from sisyphus_harness.services.evidence_contract import (
    ControlEvidenceContractService,
)

from tests.test_evidence_adjudication import (
    ResultVerifier,
    _AUTHORITY,
    _DIGEST_A,
    _DIGEST_C,
    _bundle,
    _contract,
    _job,
    _identity,
    _profile,
    _request,
)


class InvalidVerifierOutput:
    def execution_identity(self):
        return _identity()

    def execute(self, request):
        del request
        return object()

    def read_receipt(self, reference):
        del reference
        return object()


class InvalidObservationAdapter:
    adapter_digest = RECEIPT_OBSERVATION_ADAPTER_DIGEST

    def adapt(self, *, request, result, producer_authority):
        del request, result, producer_authority
        return (object(),)


class EmptyObservationAdapter:
    adapter_digest = RECEIPT_OBSERVATION_ADAPTER_DIGEST

    def adapt(self, *, request, result, producer_authority):
        del request, result, producer_authority
        return ()


class EvidenceObservationSubclass(EvidenceObservation):
    pass


class BundleVerificationRequestSubclass(BundleVerificationRequest):
    pass


class VerificationServiceResultSubclass(VerificationServiceResult):
    pass


class WrongDigestObservationAdapter:
    adapter_digest = _DIGEST_C

    def adapt(self, *, request, result, producer_authority):
        return ReceiptObservationAdapter().adapt(
            request=request,
            result=result,
            producer_authority=producer_authority,
        )


class ArtifactBackedResultVerifier(ResultVerifier):
    def __init__(self, root: Path, *, fault: str) -> None:
        super().__init__(command_passed=True)
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.fault = fault
        self.store = FilesystemVerificationEvidenceStore(root)

    def execute(self, request):
        result = super().execute(request)
        if self.fault == "missing":
            return result
        stored_receipt = result.receipt
        if self.fault == "parsed_mismatch":
            stored_receipt = replace(
                stored_receipt,
                finished_at="2026-07-20T00:00:02Z",
            )
        receipt_path = self.root / request.run_id / "receipt.json"
        write_json_atomic(receipt_path, stored_receipt.to_dict())
        reference = self.store.receipt_reference(request.run_id)
        if self.fault == "sha_mismatch":
            reference = replace(reference, sha256=_DIGEST_C)
        self.latest_result = replace(result, receipt_artifact=reference)
        return self.latest_result

    def read_receipt(self, reference):
        return self.store.read_receipt(reference)


def _bound_verification(
    *,
    command_passed: bool = True,
) -> tuple[
    BundleVerificationRequest,
    VerificationServiceResult,
    tuple[EvidenceObservation, ...],
]:
    adjudication_request = _request(_job(agent_success=True))
    request = BundleVerificationRequest(
        run_id=adjudication_request.run_id,
        workspace_bundle=adjudication_request.job_result.output_bundle,
        profile=adjudication_request.profile,
        execution_identity=_identity(),
        schema_version="sisyphus_harness.bundle_verification_request.v2",
    )
    result = ResultVerifier(command_passed=command_passed).execute(request)
    observations = ReceiptObservationAdapter().adapt(
        request=request,
        result=result,
        producer_authority=_AUTHORITY,
    )
    return request, result, observations


def _replace_first_observation(
    observations: tuple[EvidenceObservation, ...],
    replacement: EvidenceObservation,
) -> tuple[EvidenceObservation, ...]:
    return (replacement, *observations[1:])


class EvidenceBoundaryFailureTests(unittest.TestCase):
    def test_selector_rejects_non_command_facts_and_unsafe_authority(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported command observation type"):
            command_fact_selector(
                "unit-tests",
                "verification.semantic.requirement_passed",
                producer_authority=_AUTHORITY,
            )

        request, result, _ = _bound_verification()
        with self.assertRaisesRegex(ValueError, "bounded non-whitespace token"):
            ReceiptObservationAdapter().adapt(
                request=request,
                result=result,
                producer_authority="untrusted verifier",
            )

    def test_binding_validator_rejects_invalid_boundary_types(self) -> None:
        request, result, _ = _bound_verification()
        with self.assertRaisesRegex(TypeError, "verification request"):
            validate_final_verification_bindings(object(), result)  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "verification result"):
            validate_final_verification_bindings(request, object())  # type: ignore[arg-type]

        request_subclass = BundleVerificationRequestSubclass.from_dict(
            request.to_dict()
        )
        result_subclass = VerificationServiceResultSubclass.from_dict(
            result.to_dict()
        )
        with self.assertRaisesRegex(TypeError, "exact BundleVerificationRequest"):
            validate_final_verification_bindings(request_subclass, result)
        with self.assertRaisesRegex(TypeError, "exact VerificationServiceResult"):
            validate_final_verification_bindings(request, result_subclass)

    def test_binding_validator_requires_v2_and_exact_receipt_request(self) -> None:
        request, result, _ = _bound_verification()
        legacy_receipt = replace(result.receipt)
        object.__setattr__(
            legacy_receipt,
            "schema_version",
            "sisyphus_harness.verification.v2",
        )
        legacy_result = replace(result)
        object.__setattr__(legacy_result, "receipt", legacy_receipt)
        with self.assertRaisesRegex(VerificationBindingError, "digest-bound receipt"):
            validate_final_verification_bindings(request, legacy_result)

        tampered_result = replace(result)
        tampered_receipt = replace(result.receipt)
        object.__setattr__(tampered_receipt, "request_digest", _DIGEST_C)
        object.__setattr__(tampered_result, "receipt", tampered_receipt)
        with self.assertRaisesRegex(
            VerificationBindingError,
            "receipt is bound to a different request",
        ):
            validate_final_verification_bindings(request, tampered_result)

    def test_binding_validator_rejects_every_v3_service_binding_substitution(
        self,
    ) -> None:
        request, result, _ = _bound_verification()
        mutations = (
            ("workspace_bundle_id", _bundle("c").bundle_id),
            ("profile_digest", _DIGEST_C),
            ("execution_identity_digest", _DIGEST_A),
            (
                "verifier_asset_bundle_id",
                "verifier-assets:sha256:" + "f" * 64,
            ),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                receipt = replace(result.receipt)
                object.__setattr__(receipt, field, value)
                tampered = replace(result)
                object.__setattr__(tampered, "receipt", receipt)
                with self.assertRaisesRegex(
                    VerificationBindingError,
                    "service bindings are inconsistent",
                ):
                    validate_final_verification_bindings(request, tampered)

        changed_identity = replace(_identity(), image_id=_DIGEST_A)
        tampered = replace(result)
        object.__setattr__(tampered, "execution_identity", changed_identity)
        with self.assertRaisesRegex(
            VerificationBindingError,
            "different execution identity",
        ):
            validate_final_verification_bindings(request, tampered)

    def test_binding_validator_rejects_tampered_artifact_identity(self) -> None:
        request, result, _ = _bound_verification()
        tampered_result = replace(result)
        tampered_artifact = replace(
            result.receipt_artifact,
            artifact_id="another-run/receipt.json",
        )
        # VerificationServiceResult also guards this invariant.  Corrupting a
        # fresh instance models a verifier implementation that mutates its DTO
        # after construction; Control must still fail closed at its own edge.
        object.__setattr__(tampered_result, "receipt_artifact", tampered_artifact)

        with self.assertRaisesRegex(VerificationBindingError, "different run"):
            validate_final_verification_bindings(request, tampered_result)

    def test_binding_validator_rejects_empty_or_differently_shaped_commands(self) -> None:
        request, result, _ = _bound_verification()
        empty_receipt = replace(result.receipt, commands=())
        empty_result = replace(result, receipt=empty_receipt)
        with self.assertRaisesRegex(VerificationBindingError, "command count"):
            validate_final_verification_bindings(request, empty_result)

        command = result.receipt.commands[0]
        mismatches = (
            replace(command, argv=("python", "different.py")),
            replace(command, criteria=("different criterion",)),
        )
        for mismatched_command in mismatches:
            with self.subTest(command=mismatched_command):
                receipt = replace(result.receipt, commands=(mismatched_command,))
                mismatched_result = replace(result, receipt=receipt)
                with self.assertRaisesRegex(
                    VerificationBindingError,
                    "does not match the profile",
                ):
                    validate_final_verification_bindings(request, mismatched_result)

    def test_binding_validator_requires_command_chain_to_reach_receipt_state(self) -> None:
        request, result, _ = _bound_verification()
        receipt = replace(
            result.receipt,
            passed=False,
            workspace_state_after=_DIGEST_C,
            workspace_unchanged=False,
        )
        changed_result = replace(result, receipt=receipt)

        with self.assertRaisesRegex(VerificationBindingError, "final state"):
            validate_final_verification_bindings(request, changed_result)

    def test_binding_validator_recomputes_command_pass_claim(self) -> None:
        request, result, _ = _bound_verification()
        tampered_result = replace(result)
        tampered_receipt = replace(result.receipt)
        tampered_command = replace(result.receipt.commands[0])
        object.__setattr__(tampered_command, "passed", False)
        object.__setattr__(tampered_receipt, "commands", (tampered_command,))
        object.__setattr__(tampered_result, "receipt", tampered_receipt)

        with self.assertRaisesRegex(VerificationBindingError, "pass claim"):
            validate_final_verification_bindings(request, tampered_result)

        failed_request, failed_result, _ = _bound_verification(
            command_passed=False
        )
        workspace_mutation = replace(
            failed_result.receipt.commands[0],
            exit_code=0,
            workspace_state_after=_DIGEST_C,
            workspace_unchanged=False,
            failure_category="workspace_mutation",
        )
        mutation_receipt = replace(
            failed_result.receipt,
            commands=(workspace_mutation,),
            workspace_state_after=_DIGEST_C,
            workspace_unchanged=False,
        )
        mutation_result = replace(failed_result, receipt=mutation_receipt)
        validate_final_verification_bindings(failed_request, mutation_result)

    def test_binding_validator_rechecks_command_workspace_claim(self) -> None:
        request, result, _ = _bound_verification()
        tampered_result = replace(result)
        tampered_receipt = replace(result.receipt)
        tampered_command = replace(result.receipt.commands[0])
        object.__setattr__(tampered_command, "workspace_state_after", _DIGEST_C)
        object.__setattr__(tampered_receipt, "commands", (tampered_command,))
        object.__setattr__(tampered_result, "receipt", tampered_receipt)

        with self.assertRaisesRegex(
            VerificationBindingError,
            "command 0 workspace state claim",
        ):
            validate_final_verification_bindings(request, tampered_result)

    def test_binding_validator_rejects_command_name_unusable_as_evidence_id(self) -> None:
        profile = replace(
            _profile(),
            commands=(replace(_profile().commands[0], name="unit tests"),),
        )
        adjudication_request = replace(
            _request(_job(agent_success=True)),
            profile=profile,
            contract=_contract(profile),
        )
        request = BundleVerificationRequest(
            run_id=adjudication_request.run_id,
            workspace_bundle=adjudication_request.job_result.output_bundle,
            profile=profile,
            execution_identity=_identity(),
            schema_version="sisyphus_harness.bundle_verification_request.v2",
        )
        result = ResultVerifier(command_passed=True).execute(request)

        with self.assertRaisesRegex(VerificationBindingError, "cannot be used"):
            validate_final_verification_bindings(request, result)

    def test_observation_validator_rejects_invalid_type_and_count(self) -> None:
        request, result, observations = _bound_verification()
        with self.assertRaisesRegex(TypeError, "built-in tuple"):
            validate_final_command_observations(
                request,
                result,
                list(observations),  # type: ignore[arg-type]
                producer_authority=_AUTHORITY,
            )

        invalid = (object(), *observations[1:])
        with self.assertRaisesRegex(TypeError, "EvidenceObservation"):
            validate_final_command_observations(
                request,
                result,
                invalid,  # type: ignore[arg-type]
                producer_authority=_AUTHORITY,
            )

        original = observations[0]
        subclass = EvidenceObservationSubclass(
            observation_id=original.observation_id,
            selector=original.selector,
            subject_digest=original.subject_digest,
            source_run_id=original.source_run_id,
            artifact_digest=original.artifact_digest,
            status=original.status,
            value=original.value,
            reason_code=original.reason_code,
        )
        with self.assertRaisesRegex(TypeError, "exact EvidenceObservation"):
            validate_final_command_observations(
                request,
                result,
                (subclass, *observations[1:]),
                producer_authority=_AUTHORITY,
            )

        for incomplete in ((), observations[:-1]):
            with self.subTest(count=len(incomplete)):
                with self.assertRaisesRegex(VerificationBindingError, "count"):
                    validate_final_command_observations(
                        request,
                        result,
                        incomplete,
                        producer_authority=_AUTHORITY,
                    )

    def test_observation_validator_rejects_every_lineage_mismatch(self) -> None:
        request, result, observations = _bound_verification()
        first = observations[0]
        mutations = (
            (
                replace(first, subject_digest=_DIGEST_C),
                "different workspace bundle",
            ),
            (replace(first, source_run_id="another-run"), "different run"),
            (replace(first, artifact_digest=_DIGEST_C), "different artifact"),
            (
                replace(
                    first,
                    selector=replace(first.selector, stage="candidate_intermediate"),
                ),
                "candidate-final",
            ),
            (
                replace(
                    first,
                    selector=replace(
                        first.selector,
                        producer_authority="foreign.verifier",
                    ),
                ),
                "different producer",
            ),
        )
        for mutation, message in mutations:
            with self.subTest(message=message):
                with self.assertRaisesRegex(VerificationBindingError, message):
                    validate_final_command_observations(
                        request,
                        result,
                        _replace_first_observation(observations, mutation),
                        producer_authority=_AUTHORITY,
                    )

    def test_observation_validator_rejects_duplicate_and_missing_selectors(self) -> None:
        request, result, observations = _bound_verification()
        duplicated = (observations[0], observations[0], *observations[2:])
        with self.assertRaisesRegex(VerificationBindingError, "duplicated"):
            validate_final_command_observations(
                request,
                result,
                duplicated,
                producer_authority=_AUTHORITY,
            )

        wrong_check = replace(
            observations[0],
            selector=replace(observations[0].selector, check_id="another-command"),
        )
        with self.assertRaisesRegex(VerificationBindingError, "selector"):
            validate_final_command_observations(
                request,
                result,
                _replace_first_observation(observations, wrong_check),
                producer_authority=_AUTHORITY,
            )

    def test_observation_validator_rejects_status_type_and_value_substitution(self) -> None:
        request, result, observations = _bound_verification()
        passed = observations[0]
        mutations = (
            replace(
                passed,
                status=ObservationStatus.ERROR,
                value=None,
                reason_code="normalization_error",
            ),
            replace(passed, value=1),
            replace(passed, value=False),
        )
        for mutation in mutations:
            with self.subTest(observation=mutation):
                with self.assertRaisesRegex(
                    VerificationBindingError,
                    "does not match the receipt",
                ):
                    validate_final_command_observations(
                        request,
                        result,
                        _replace_first_observation(observations, mutation),
                        producer_authority=_AUTHORITY,
                    )

    def test_exit_code_unavailable_is_explicit_and_cannot_be_fabricated(self) -> None:
        request, result, _ = _bound_verification(command_passed=False)
        command = replace(result.receipt.commands[0], exit_code=None)
        receipt = replace(result.receipt, commands=(command,))
        unavailable_result = replace(result, receipt=receipt)
        observations = ReceiptObservationAdapter().adapt(
            request=request,
            result=unavailable_result,
            producer_authority=_AUTHORITY,
        )
        exit_code = next(
            observation
            for observation in observations
            if observation.selector.observation_type == COMMAND_EXIT_CODE
        )

        self.assertIs(exit_code.status, ObservationStatus.UNAVAILABLE)
        self.assertIsNone(exit_code.value)
        self.assertEqual(exit_code.reason_code, "command_not_started")
        validate_final_command_observations(
            request,
            unavailable_result,
            observations,
            producer_authority=_AUTHORITY,
        )

        fabricated = replace(
            exit_code,
            status=ObservationStatus.OBSERVED,
            value=0,
            reason_code=None,
        )
        replaced_observations = tuple(
            fabricated if item is exit_code else item for item in observations
        )
        with self.assertRaisesRegex(VerificationBindingError, "normalized incorrectly"):
            validate_final_command_observations(
                request,
                unavailable_result,
                replaced_observations,
                producer_authority=_AUTHORITY,
            )

    def test_adjudication_request_enforces_control_owned_input_types(self) -> None:
        request = _request(_job(agent_success=True))
        invalid_fields = (
            ("job_result", object(), "job result"),
            ("profile", object(), "profile"),
            ("contract", object(), "contract"),
            ("run_id", "..", "run ID"),
        )
        for field_name, value, message in invalid_fields:
            with self.subTest(field=field_name):
                with self.assertRaisesRegex((TypeError, ValueError), message):
                    replace(request, **{field_name: value})

        invalid_authorities = (
            object(),
            "",
            "a" * 257,
            " leading",
            "internal space",
            "control\x01character",
        )
        for authority in invalid_authorities:
            with self.subTest(authority=authority):
                with self.assertRaisesRegex(ValueError, "bounded token"):
                    replace(request, producer_authority=authority)

    def test_contract_is_pinned_to_profile_and_observation_adapter(self) -> None:
        request = _request(_job(agent_success=True))
        substituted_profile = replace(
            request.profile,
            commands=(
                replace(
                    request.profile.commands[0],
                    argv=("/usr/bin/true",),
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "profile does not match"):
            replace(request, profile=substituted_profile)

        verifier = ResultVerifier(command_passed=True)
        service = ControlEvidenceContractService(
            verifier,
            WrongDigestObservationAdapter(),
        )
        with self.assertRaisesRegex(
            VerificationBindingError,
            "adapter does not match",
        ):
            service.adjudicate(request)
        self.assertEqual(verifier.requests, [])

    def test_control_requires_the_authoritative_receipt_artifact(self) -> None:
        cases = (
            ("missing", VerificationEvidenceError, "cannot be read"),
            ("sha_mismatch", VerificationEvidenceError, "digest does not match"),
            ("parsed_mismatch", VerificationBindingError, "does not match"),
        )
        for fault, error_type, message in cases:
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                service = ControlEvidenceContractService(
                    ArtifactBackedResultVerifier(Path(directory), fault=fault)
                )
                with self.assertRaisesRegex(error_type, message):
                    service.adjudicate(_request(_job(agent_success=True)))

    def test_adjudication_result_enforces_immutable_shadow_result_types(self) -> None:
        valid = ControlEvidenceContractService(
            ResultVerifier(command_passed=True)
        ).adjudicate(_request(_job(agent_success=True)))

        for field_name in ("job_id", "attempt_id", "output_bundle_id"):
            with self.subTest(empty_identity=field_name):
                with self.assertRaisesRegex(ValueError, "identity"):
                    replace(valid, **{field_name: ""})

        invalid_fields = (
            ("agent_reported_success", 1, "success"),
            ("verification_request", object(), "request"),
            ("verification_result", object(), "result"),
            ("observations", (object(),), "observations"),
            ("evaluation", object(), "evaluation"),
        )
        for field_name, value, message in invalid_fields:
            with self.subTest(field=field_name):
                with self.assertRaisesRegex(TypeError, message):
                    replace(valid, **{field_name: value})

        with self.assertRaisesRegex(TypeError, "built-in tuple"):
            replace(valid, observations=list(valid.observations))
        original = valid.observations[0]
        subclass = EvidenceObservationSubclass(
            observation_id=original.observation_id,
            selector=original.selector,
            subject_digest=original.subject_digest,
            source_run_id=original.source_run_id,
            artifact_digest=original.artifact_digest,
            status=original.status,
            value=original.value,
            reason_code=original.reason_code,
        )
        with self.assertRaisesRegex(TypeError, "exact"):
            replace(valid, observations=(subclass, *valid.observations[1:]))

    def test_service_rejects_invalid_collaborators_request_and_outputs(self) -> None:
        with self.assertRaisesRegex(TypeError, "verifier"):
            ControlEvidenceContractService(object())  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "observation adapter"):
            ControlEvidenceContractService(
                ResultVerifier(command_passed=True),
                object(),  # type: ignore[arg-type]
            )

        service = ControlEvidenceContractService(ResultVerifier(command_passed=True))
        with self.assertRaisesRegex(TypeError, "adjudication request"):
            service.adjudicate(object())  # type: ignore[arg-type]

        with self.assertRaisesRegex(TypeError, "verification result"):
            ControlEvidenceContractService(InvalidVerifierOutput()).adjudicate(
                _request(_job(agent_success=True))
            )

        for adapter, message in (
            (InvalidObservationAdapter(), "EvidenceObservation"),
            (EmptyObservationAdapter(), "count"),
        ):
            with self.subTest(adapter=type(adapter).__name__):
                service = ControlEvidenceContractService(
                    ResultVerifier(command_passed=True),
                    adapter,
                )
                with self.assertRaisesRegex((TypeError, VerificationBindingError), message):
                    service.adjudicate(_request(_job(agent_success=True)))

    def test_command_fact_selector_builds_the_control_stage(self) -> None:
        selector = command_fact_selector(
            "unit-tests",
            COMMAND_PASSED,
            producer_authority=_AUTHORITY,
        )
        self.assertEqual(selector.stage, FINAL_VERIFICATION_STAGE)


if __name__ == "__main__":
    unittest.main()
