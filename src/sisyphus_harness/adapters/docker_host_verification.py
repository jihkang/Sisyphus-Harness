from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
import subprocess
import time
from typing import Callable, Protocol

from ..contracts.codec import loads_strict_json
from ..contracts.verification import (
    CommandResult,
    CommandSpec,
    VerificationReceipt,
    VerificationRequest,
)
from ..contracts.verification_service import (
    BundleVerificationRequest,
    VerificationServiceResult,
    VerifierExecutionIdentity,
)
from ..infra.verification_evidence import FilesystemVerificationEvidenceStore
from ..infra.verifier_assets import verifier_asset_tree_hash
from ..infra.workspace_bundle import workspace_tree_hash
from ..receipts import write_json_atomic, write_text_atomic
from ..verifier import classify_command_failure
from .docker_runtime import DockerVerifierError, _CommandCapture
from .receipt_observations import (
    VerificationBindingError,
    validate_final_verification_bindings,
)


class HostCommandRuntime(Protocol):
    max_output_bytes: int

    def _probe_executable(
        self,
        specification: CommandSpec,
        *,
        workspace: Path,
        asset_view: Path | None,
        cidfile: Path,
        execution_identity: VerifierExecutionIdentity,
        timeout_seconds: float,
    ) -> tuple[str | None, str | None, str | None]: ...

    def _capture_command(
        self,
        specification: CommandSpec,
        *,
        workspace: Path,
        asset_view: Path | None,
        cidfile: Path,
        execution_identity: VerifierExecutionIdentity,
        timeout_seconds: float,
    ) -> _CommandCapture: ...

    def _remove_container(self, cidfile: Path) -> None: ...


@dataclass(frozen=True, slots=True)
class DockerHostVerifier:
    runtime: HostCommandRuntime
    workspace_hash: Callable[[Path], str] = workspace_tree_hash
    asset_hash: Callable[[Path], str] = verifier_asset_tree_hash
    validate_bindings: Callable[
        [BundleVerificationRequest, VerificationServiceResult], None
    ] = validate_final_verification_bindings
    monotonic: Callable[[], float] = time.monotonic
    now: Callable[[], str] = lambda: datetime.now(UTC).isoformat()

    def execute(
        self,
        request: BundleVerificationRequest,
        *,
        workspace: Path,
        staging_root: Path,
        asset_view: Path | None,
        staging_directory: Path,
        timeout_seconds: float,
    ) -> VerificationServiceResult:
        baseline = self.workspace_hash(workspace)
        if baseline != request.workspace_bundle.tree_hash:
            raise DockerVerifierError(
                "host verifier workspace does not match the requested tree"
            )
        expected_asset_tree = (
            request.profile.asset_bundle.tree_hash
            if request.profile.asset_bundle is not None
            else None
        )
        if asset_view is not None:
            asset_tree = self.asset_hash(asset_view)
            if asset_tree != expected_asset_tree:
                raise DockerVerifierError(
                    "host verifier asset view does not match the requested tree"
                )

        run_directory = staging_root / request.run_id
        run_directory.mkdir()
        write_json_atomic(
            run_directory / "request.json",
            VerificationRequest(
                run_id=request.run_id,
                workspace="/workspace",
                workspace_state_before=baseline,
                commands=request.profile.commands,
            ).to_dict(),
        )
        started_at = self.now()
        deadline = self.monotonic() + timeout_seconds
        command_results = tuple(
            self.execute_command(
                request,
                specification,
                index=index,
                workspace=workspace,
                run_directory=run_directory,
                asset_view=asset_view,
                expected_asset_tree=expected_asset_tree,
                staging_directory=staging_directory,
                deadline=deadline,
            )
            for index, specification in enumerate(request.profile.commands)
        )

        final_state = self.workspace_hash(workspace)
        workspace_unchanged = baseline == final_state
        receipt = VerificationReceipt(
            run_id=request.run_id,
            workspace="/workspace",
            worktree_commit_sha=request.workspace_bundle.source_commit_sha,
            started_at=started_at,
            finished_at=self.now(),
            passed=workspace_unchanged and all(
                result.passed for result in command_results
            ),
            commands=command_results,
            workspace_state_before=baseline,
            workspace_state_after=final_state,
            workspace_unchanged=workspace_unchanged,
            request_digest=request.request_digest,
            schema_version="sisyphus_harness.verification.v3",
            workspace_bundle_id=request.workspace_bundle.bundle_id,
            profile_digest=request.profile.profile_digest,
            execution_identity_digest=request.execution_identity.identity_digest,
            verifier_asset_bundle_id=(
                request.profile.asset_bundle.bundle_id
                if request.profile.asset_bundle is not None
                else None
            ),
        )
        write_json_atomic(run_directory / "receipt.json", receipt.to_dict())
        receipt_artifact = FilesystemVerificationEvidenceStore(
            staging_root
        ).receipt_reference(request.run_id)
        result = VerificationServiceResult(
            request_digest=request.request_digest,
            workspace_bundle_id=request.workspace_bundle.bundle_id,
            profile_digest=request.profile.profile_digest,
            receipt=receipt,
            receipt_artifact=receipt_artifact,
            execution_identity=request.execution_identity,
            schema_version="sisyphus_harness.verification_service_result.v2",
        )
        try:
            self.validate_bindings(request, result)
        except VerificationBindingError as exc:
            raise DockerVerifierError(str(exc)) from exc
        return result

    def execute_command(
        self,
        request: BundleVerificationRequest,
        specification: CommandSpec,
        *,
        index: int,
        workspace: Path,
        run_directory: Path,
        asset_view: Path | None,
        expected_asset_tree: str | None,
        staging_directory: Path,
        deadline: float,
    ) -> CommandResult:
        before = self.workspace_hash(workspace)
        command_directory = run_directory / f"{index:02d}-{_safe_name(specification.name)}"
        command_directory.mkdir()
        stdout_path = command_directory / "stdout.txt"
        stderr_path = command_directory / "stderr.txt"
        remaining = deadline - self.monotonic()
        executable_path: str | None = None
        executable_sha256: str | None = None
        if remaining <= 0:
            capture = _CommandCapture(
                returncode=None,
                stdout="",
                stderr="global verification deadline exceeded\n",
                timed_out=True,
            )
        else:
            probe_cidfile = staging_directory / f"command-{index:04d}.probe.cid"
            try:
                executable_path, executable_sha256, probe_error = (
                    self.runtime._probe_executable(
                        specification,
                        workspace=workspace,
                        asset_view=asset_view,
                        cidfile=probe_cidfile,
                        execution_identity=request.execution_identity,
                        timeout_seconds=min(remaining, 30.0),
                    )
                )
            except subprocess.TimeoutExpired:
                self.runtime._remove_container(probe_cidfile)
                capture = _CommandCapture(
                    returncode=None,
                    stdout="",
                    stderr="verification executable probe timed out\n",
                    timed_out=True,
                )
            else:
                if probe_error is not None:
                    capture = _CommandCapture(
                        returncode=None,
                        stdout="",
                        stderr=f"{probe_error}\n",
                        launch_error=probe_error,
                    )
                else:
                    command_cidfile = staging_directory / f"command-{index:04d}.cid"
                    capture = self.runtime._capture_command(
                        specification,
                        workspace=workspace,
                        asset_view=asset_view,
                        cidfile=command_cidfile,
                        execution_identity=request.execution_identity,
                        timeout_seconds=min(
                            specification.timeout_seconds,
                            max(0.001, deadline - self.monotonic()),
                        ),
                    )

        write_text_atomic(stdout_path, capture.stdout)
        write_text_atomic(stderr_path, capture.stderr)
        after = self.workspace_hash(workspace)
        workspace_unchanged = before == after
        if asset_view is not None:
            asset_tree_after = self.asset_hash(asset_view)
            if asset_tree_after != expected_asset_tree:
                raise DockerVerifierError(
                    "verifier asset view changed during candidate execution"
                )
        passed = (
            capture.launch_error is None
            and not capture.timed_out
            and not capture.output_limited
            and capture.returncode == 0
            and workspace_unchanged
        )
        failure_category = classify_command_failure(
            passed=passed,
            timed_out=capture.timed_out,
            output_limited=capture.output_limited,
            process_leaked=False,
            launch_error=capture.launch_error,
            workspace_unchanged=workspace_unchanged,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        error = capture.launch_error
        if capture.output_limited:
            error = (
                f"verification output exceeded {self.runtime.max_output_bytes} bytes"
            )
        elif capture.timed_out:
            error = "verification command exceeded its bounded deadline"
        return CommandResult(
            name=specification.name,
            argv=specification.argv,
            criteria=specification.criteria,
            passed=passed,
            timed_out=capture.timed_out,
            exit_code=capture.returncode,
            duration_ms=capture.duration_ms,
            executable_path=executable_path,
            executable_sha256=executable_sha256,
            stdout_path=stdout_path.relative_to(run_directory).as_posix(),
            stderr_path=stderr_path.relative_to(run_directory).as_posix(),
            workspace_state_before=before,
            workspace_state_after=after,
            workspace_unchanged=workspace_unchanged,
            failure_category=failure_category,
            error=error,
        )


def parse_legacy_result(
    completed: subprocess.CompletedProcess[str],
    request: BundleVerificationRequest,
    *,
    validate_bindings: Callable[
        [BundleVerificationRequest, VerificationServiceResult], None
    ] = validate_final_verification_bindings,
) -> VerificationServiceResult:
    if completed.returncode not in {0, 1}:
        detail = completed.stderr.strip()[-2000:]
        raise DockerVerifierError(detail or "verifier container failed")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise DockerVerifierError("verifier container returned no result")
    try:
        raw = loads_strict_json(lines[-1], label="verifier container result")
        result = VerificationServiceResult.from_dict(raw)
    except ValueError as exc:
        raise DockerVerifierError(str(exc)) from exc
    if result.request_digest != request.request_digest:
        raise DockerVerifierError("verifier result is bound to a different request")
    if result.workspace_bundle_id != request.workspace_bundle.bundle_id:
        raise DockerVerifierError(
            "verifier result is bound to a different workspace bundle"
        )
    if result.profile_digest != request.profile.profile_digest:
        raise DockerVerifierError(
            "verifier result is bound to a different verification profile"
        )
    if result.execution_identity != request.execution_identity:
        raise DockerVerifierError(
            "verifier result is bound to a different execution identity"
        )
    if result.receipt.run_id != request.run_id:
        raise DockerVerifierError("verifier result is bound to a different run")
    try:
        validate_bindings(request, result)
    except VerificationBindingError as exc:
        raise DockerVerifierError(str(exc)) from exc
    return result


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return normalized or "command"
