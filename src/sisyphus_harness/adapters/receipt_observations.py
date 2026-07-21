from __future__ import annotations

from dataclasses import dataclass

from ..contracts.codec import sha256_digest
from ..contracts.evidence_contract import (
    EvidenceObservation,
    EvidenceSelector,
    ObservationStatus,
)
from ..contracts.verification import CommandResult
from ..contracts.verification_service import (
    BundleVerificationRequest,
    VerificationServiceResult,
)


FINAL_VERIFICATION_STAGE = "candidate_final"
COMMAND_PASSED = "verification.command.passed"
COMMAND_EXIT_CODE = "verification.command.exit_code"
COMMAND_TIMED_OUT = "verification.command.timed_out"
COMMAND_WORKSPACE_UNCHANGED = "verification.command.workspace_unchanged"
COMMAND_FAILURE_CATEGORY = "verification.command.failure_category"
NO_FAILURE_CATEGORY = "none"
VERIFICATION_RECEIPT_MEDIA_TYPE = (
    "application/vnd.sisyphus-harness.verification-receipt+json"
)

COMMAND_FACT_TYPES = (
    COMMAND_PASSED,
    COMMAND_EXIT_CODE,
    COMMAND_TIMED_OUT,
    COMMAND_WORKSPACE_UNCHANGED,
    COMMAND_FAILURE_CATEGORY,
)
RECEIPT_OBSERVATION_ADAPTER_VERSION = (
    "sisyphus_harness.receipt_observation_adapter.v1"
)
RECEIPT_OBSERVATION_ADAPTER_DIGEST = sha256_digest(
    {
        "version": RECEIPT_OBSERVATION_ADAPTER_VERSION,
        "stage": FINAL_VERIFICATION_STAGE,
        "fact_types": list(COMMAND_FACT_TYPES),
        "criteria_projection": False,
        "raw_output_parsing": False,
        "selector_cardinality": "one-per-command-fact",
    }
)


class VerificationBindingError(ValueError):
    """The verifier result is not bound to the exact Control request."""


def command_fact_selector(
    command_name: str,
    observation_type: str,
    *,
    producer_authority: str,
) -> EvidenceSelector:
    """Build the stable selector used by receipt observations and contracts."""

    if observation_type not in COMMAND_FACT_TYPES:
        raise ValueError("unsupported command observation type")
    return EvidenceSelector(
        observation_type=observation_type,
        stage=FINAL_VERIFICATION_STAGE,
        check_id=command_name,
        producer_authority=producer_authority,
    )


def validate_final_verification_bindings(
    request: BundleVerificationRequest,
    result: VerificationServiceResult,
    *,
    require_verifier_assets: bool = False,
) -> None:
    """Fail closed unless request, bundle, profile, run, and receipt agree."""

    if type(request) is not BundleVerificationRequest:
        raise TypeError(
            "verification request must be an exact BundleVerificationRequest"
        )
    if type(result) is not VerificationServiceResult:
        raise TypeError(
            "verification result must be an exact VerificationServiceResult"
        )
    if result.request_digest != request.request_digest:
        raise VerificationBindingError(
            "verification result is bound to a different request"
        )
    if result.workspace_bundle_id != request.workspace_bundle.bundle_id:
        raise VerificationBindingError(
            "verification result is bound to a different workspace bundle"
        )
    if result.profile_digest != request.profile.profile_digest:
        raise VerificationBindingError(
            "verification result is bound to a different profile"
        )

    service_v2 = (
        request.schema_version
        == "sisyphus_harness.bundle_verification_request.v2"
    )
    if service_v2:
        if (
            result.schema_version
            != "sisyphus_harness.verification_service_result.v2"
            or request.execution_identity is None
            or result.execution_identity != request.execution_identity
        ):
            raise VerificationBindingError(
                "verification result is bound to a different execution identity"
            )
        if require_verifier_assets and request.profile.asset_bundle is None:
            raise VerificationBindingError(
                "Control final verification requires verifier-owned assets"
            )
    elif (
        result.schema_version != "sisyphus_harness.verification_service_result.v1"
        or result.execution_identity is not None
    ):
        raise VerificationBindingError(
            "legacy verification result has an unexpected execution identity"
        )

    receipt = result.receipt
    expected_receipt_schema = (
        "sisyphus_harness.verification.v3"
        if service_v2
        else "sisyphus_harness.verification.v2"
    )
    if receipt.schema_version != expected_receipt_schema:
        raise VerificationBindingError(
            "final verification requires the matching digest-bound receipt schema"
        )
    if receipt.run_id != request.run_id:
        raise VerificationBindingError(
            "verification receipt is bound to a different run"
        )
    if receipt.request_digest != request.request_digest:
        raise VerificationBindingError(
            "verification receipt is bound to a different request"
        )
    if service_v2:
        assert request.execution_identity is not None
        asset_bundle = request.profile.asset_bundle
        if (
            receipt.workspace_bundle_id != request.workspace_bundle.bundle_id
            or receipt.profile_digest != request.profile.profile_digest
            or receipt.execution_identity_digest
            != request.execution_identity.identity_digest
            or receipt.verifier_asset_bundle_id
            != (asset_bundle.bundle_id if asset_bundle is not None else None)
        ):
            raise VerificationBindingError(
                "verification receipt service bindings are inconsistent"
            )
    if receipt.worktree_commit_sha != request.workspace_bundle.source_commit_sha:
        raise VerificationBindingError(
            "verification receipt is bound to a different source commit"
        )
    if receipt.workspace_state_before != request.workspace_bundle.tree_hash:
        raise VerificationBindingError(
            "verification receipt is bound to a different workspace tree"
        )
    if receipt.workspace_unchanged != (
        receipt.workspace_state_before == receipt.workspace_state_after
    ):
        raise VerificationBindingError(
            "verification receipt workspace state claim is inconsistent"
        )
    if result.receipt_artifact.artifact_id != f"{request.run_id}/receipt.json":
        raise VerificationBindingError(
            "verification receipt artifact is bound to a different run"
        )
    if result.receipt_artifact.media_type != VERIFICATION_RECEIPT_MEDIA_TYPE:
        raise VerificationBindingError(
            "verification receipt artifact has an unsupported media type"
        )

    specs = request.profile.commands
    command_results = receipt.commands
    if len(specs) != len(command_results):
        raise VerificationBindingError(
            "verification receipt command count does not match the profile"
        )
    previous_state = receipt.workspace_state_before
    for index, (spec, command) in enumerate(
        zip(specs, command_results, strict=True)
    ):
        # Criteria remain transport binding data only.  They are deliberately
        # never projected into EvidenceObservation values or selectors.
        if (
            command.name != spec.name
            or command.argv != spec.argv
            or command.criteria != spec.criteria
        ):
            raise VerificationBindingError(
                f"verification receipt command {index} does not match the profile"
            )
        command_should_pass = (
            not command.timed_out
            and command.exit_code == 0
            and command.workspace_unchanged
            and command.failure_category is None
            and command.error is None
        )
        if command.passed != command_should_pass:
            raise VerificationBindingError(
                f"verification receipt command {index} pass claim is inconsistent"
            )
        if command.workspace_unchanged != (
            command.workspace_state_before == command.workspace_state_after
        ):
            raise VerificationBindingError(
                f"verification receipt command {index} workspace state claim "
                "is inconsistent"
            )
        if command.workspace_state_before != previous_state:
            raise VerificationBindingError(
                f"verification receipt command {index} breaks the workspace "
                "state chain"
            )
        previous_state = command.workspace_state_after
        try:
            command_fact_selector(
                command.name,
                COMMAND_PASSED,
                producer_authority="binding-check",
            )
        except ValueError as exc:
            raise VerificationBindingError(
                f"verification command {index} cannot be used as a check ID"
            ) from exc
    if previous_state != receipt.workspace_state_after:
        raise VerificationBindingError(
            "verification receipt command chain does not reach the final state"
        )


def validate_final_command_observations(
    request: BundleVerificationRequest,
    result: VerificationServiceResult,
    observations: tuple[EvidenceObservation, ...],
    *,
    producer_authority: str,
) -> None:
    """Validate normalized facts before they reach the pure evaluator."""

    if type(observations) is not tuple:
        raise TypeError("receipt observations must be an immutable built-in tuple")
    values = observations
    if any(type(item) is not EvidenceObservation for item in values):
        raise TypeError("receipt observations must be exact EvidenceObservation values")
    expected_count = len(result.receipt.commands) * len(COMMAND_FACT_TYPES)
    if len(values) != expected_count:
        raise VerificationBindingError(
            "receipt observation count does not match command facts"
        )
    by_selector: dict[tuple[str, str], EvidenceObservation] = {}
    for observation in values:
        if observation.subject_digest != request.workspace_bundle.archive_sha256:
            raise VerificationBindingError(
                "receipt observation is bound to a different workspace bundle"
            )
        if observation.source_run_id != request.run_id:
            raise VerificationBindingError(
                "receipt observation is bound to a different run"
            )
        if observation.artifact_digest != result.receipt_artifact.sha256:
            raise VerificationBindingError(
                "receipt observation is bound to a different artifact"
            )
        selector = observation.selector
        if selector.stage != FINAL_VERIFICATION_STAGE:
            raise VerificationBindingError(
                "receipt observation is not candidate-final evidence"
            )
        if selector.producer_authority != producer_authority:
            raise VerificationBindingError(
                "receipt observation is bound to a different producer"
            )
        key = (selector.check_id, selector.observation_type)
        if key in by_selector:
            raise VerificationBindingError("receipt observation selector is duplicated")
        by_selector[key] = observation

    for command in result.receipt.commands:
        expected = {
            COMMAND_PASSED: command.passed,
            COMMAND_EXIT_CODE: command.exit_code,
            COMMAND_TIMED_OUT: command.timed_out,
            COMMAND_WORKSPACE_UNCHANGED: command.workspace_unchanged,
            COMMAND_FAILURE_CATEGORY: (
                command.failure_category or NO_FAILURE_CATEGORY
            ),
        }
        for observation_type, expected_value in expected.items():
            observation = by_selector.get((command.name, observation_type))
            if observation is None:
                raise VerificationBindingError(
                    "receipt observation selector does not match command facts"
                )
            if expected_value is None:
                if (
                    observation.status is not ObservationStatus.UNAVAILABLE
                    or observation.value is not None
                ):
                    raise VerificationBindingError(
                        "unavailable command fact was normalized incorrectly"
                    )
            elif (
                observation.status is not ObservationStatus.OBSERVED
                or type(observation.value) is not type(expected_value)
                or observation.value != expected_value
            ):
                raise VerificationBindingError(
                    "observed command fact does not match the receipt"
                )


@dataclass(frozen=True, slots=True)
class ReceiptObservationAdapter:
    """Translate a bound receipt into command facts, never semantic claims."""

    @property
    def adapter_digest(self) -> str:
        return RECEIPT_OBSERVATION_ADAPTER_DIGEST

    def adapt(
        self,
        *,
        request: BundleVerificationRequest,
        result: VerificationServiceResult,
        producer_authority: str,
    ) -> tuple[EvidenceObservation, ...]:
        validate_final_verification_bindings(request, result)
        # Validate authority even for an empty/future receipt before iterating.
        command_fact_selector(
            "authority-check",
            COMMAND_PASSED,
            producer_authority=producer_authority,
        )
        observations: list[EvidenceObservation] = []
        for index, command in enumerate(result.receipt.commands):
            observations.extend(
                self._command_observations(
                    request=request,
                    result=result,
                    command=command,
                    command_index=index,
                    producer_authority=producer_authority,
                )
            )
        return tuple(observations)

    def _command_observations(
        self,
        *,
        request: BundleVerificationRequest,
        result: VerificationServiceResult,
        command: CommandResult,
        command_index: int,
        producer_authority: str,
    ) -> tuple[EvidenceObservation, ...]:
        common = {
            "subject_digest": request.workspace_bundle.archive_sha256,
            "source_run_id": result.receipt.run_id,
            "artifact_digest": result.receipt_artifact.sha256,
        }
        prefix = f"{result.receipt.run_id}.command-{command_index:04d}"

        def observed(
            suffix: str,
            observation_type: str,
            value: bool | int | str,
        ) -> EvidenceObservation:
            return EvidenceObservation(
                observation_id=f"{prefix}.{suffix}",
                selector=command_fact_selector(
                    command.name,
                    observation_type,
                    producer_authority=producer_authority,
                ),
                status=ObservationStatus.OBSERVED,
                value=value,
                reason_code=None,
                **common,
            )

        if command.exit_code is None:
            exit_code = EvidenceObservation(
                observation_id=f"{prefix}.exit-code",
                selector=command_fact_selector(
                    command.name,
                    COMMAND_EXIT_CODE,
                    producer_authority=producer_authority,
                ),
                status=ObservationStatus.UNAVAILABLE,
                value=None,
                reason_code="command_not_started",
                **common,
            )
        else:
            exit_code = observed(
                "exit-code",
                COMMAND_EXIT_CODE,
                command.exit_code,
            )
        return (
            observed("passed", COMMAND_PASSED, command.passed),
            exit_code,
            observed("timed-out", COMMAND_TIMED_OUT, command.timed_out),
            observed(
                "workspace-unchanged",
                COMMAND_WORKSPACE_UNCHANGED,
                command.workspace_unchanged,
            ),
            observed(
                "failure-category",
                COMMAND_FAILURE_CATEGORY,
                command.failure_category or NO_FAILURE_CATEGORY,
            ),
        )
