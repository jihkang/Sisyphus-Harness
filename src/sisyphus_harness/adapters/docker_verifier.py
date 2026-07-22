from __future__ import annotations

from dataclasses import dataclass, replace
import math
import os
from pathlib import Path
import subprocess
import tempfile

from ..contracts.artifacts import ArtifactRef
from ..contracts.verification import CommandResult, CommandSpec, VerificationReceipt
from ..contracts.verification_service import (
    BundleVerificationRequest,
    VerificationServiceResult,
    VerifierExecutionIdentity,
)
from ..contracts.workspace import WorkspaceBundleRef
from ..infra.verification_evidence import (
    FilesystemVerificationEvidenceStore,
    VerificationEvidenceError,
)
from ..infra.verifier_assets import (
    FilesystemVerifierAssetBundleStore,
    VerifierAssetError,
    verifier_asset_tree_hash,
)
from ..infra.workspace_bundle import (
    FilesystemWorkspaceBundleStore,
    WorkspaceBundleError,
    workspace_tree_hash,
)
from ..receipts import write_json_atomic
from .docker_bundle_view import prepare_bundle_view, same_stable_file
from .docker_evidence import DockerEvidencePublisher, fsync_directory
from .docker_host_verification import (
    DockerHostVerifier,
    parse_legacy_result,
)
from .docker_runtime import (
    DockerProcessRunner,
    DockerRuntime,
    DockerVerifierError,
    _CommandCapture,
    _DockerOutputLimitError as _DockerOutputLimitError,
    docker_bind_mount,
    remove_container,
)
from .receipt_observations import validate_final_verification_bindings


# Compatibility patch points for existing low-level regression tests. New code
# belongs to the owning collaborator modules imported above.
_same_stable_file = same_stable_file
_docker_bind_mount = docker_bind_mount
_fsync_directory = fsync_directory


@dataclass(frozen=True, slots=True)
class _TransportProcessPort:
    transport: DockerVerifierTransport

    def run(
        self,
        command: list[str],
        *,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        bounded = replace(
            self.transport,
            timeout_seconds=min(self.transport.timeout_seconds, timeout_seconds),
        )
        return bounded._run_container(command)

    def remove_container(self, cidfile: Path) -> None:
        self.transport._remove_container(cidfile)


@dataclass(frozen=True, slots=True)
class DockerVerifierTransport:
    """Public Docker verification facade with host-owned evidence authority."""

    bundle_store: Path
    artifact_root: Path
    asset_store: Path | None = None
    image: str = "sisyphus-harness-verifier:local"
    timeout_seconds: float = 300
    memory: str = "512m"
    cpus: str = "1.0"
    pids_limit: int = 64
    max_output_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("Docker verifier timeout must be positive")
        if (
            isinstance(self.max_output_bytes, bool)
            or not isinstance(self.max_output_bytes, int)
            or self.max_output_bytes <= 0
        ):
            raise ValueError("Docker verifier output limit must be positive")
        if (
            not self.image
            or len(self.image) > 512
            or self.image.startswith("-")
            or any(character.isspace() or ord(character) < 32 for character in self.image)
        ):
            raise ValueError("Docker verifier image reference is unsafe")

    def execution_identity(self) -> VerifierExecutionIdentity:
        return self._runtime().execution_identity()

    def execute_with_timeout(
        self,
        request: BundleVerificationRequest,
        *,
        timeout_seconds: float,
    ) -> VerificationServiceResult:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Docker verifier timeout override must be positive")
        bounded = replace(
            self,
            timeout_seconds=min(self.timeout_seconds, timeout_seconds),
        )
        return bounded.execute(request)

    def execute(self, request: BundleVerificationRequest) -> VerificationServiceResult:
        if (
            request.schema_version
            != "sisyphus_harness.bundle_verification_request.v2"
            or request.execution_identity is None
        ):
            raise DockerVerifierError(
                "Docker verifier requires an execution-bound v2 request"
            )
        if self.execution_identity() != request.execution_identity:
            raise DockerVerifierError(
                "verifier image identity changed after request admission"
            )
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".sisyphus-verifier-staging-",
            dir=self.artifact_root.parent,
        ) as directory:
            staging_directory = Path(directory)
            staging_root = staging_directory / "artifacts"
            staging_root.mkdir()
            bundle_view = staging_directory / "bundles"
            self._prepare_bundle_view(request.workspace_bundle, bundle_view)
            workspace = staging_directory / "workspace"
            try:
                materialized_hash = FilesystemWorkspaceBundleStore(
                    bundle_view
                ).materialize(request.workspace_bundle, workspace)
            except WorkspaceBundleError as exc:
                raise DockerVerifierError(
                    "workspace bundle failed isolated materialization"
                ) from exc
            if materialized_hash != request.workspace_bundle.tree_hash:
                raise DockerVerifierError(
                    "materialized workspace does not match the requested tree"
                )
            asset_view = self._materialize_assets(request, staging_directory)
            result = self._execute_host_owned(
                request,
                workspace=workspace,
                staging_root=staging_root,
                asset_view=asset_view,
                staging_directory=staging_directory,
            )
            try:
                staged_receipt = FilesystemVerificationEvidenceStore(
                    staging_root
                ).read_receipt(result.receipt_artifact)
            except VerificationEvidenceError as exc:
                raise DockerVerifierError(
                    "verifier receipt artifact failed host validation"
                ) from exc
            if staged_receipt != result.receipt:
                raise DockerVerifierError(
                    "verifier result does not match its receipt artifact"
                )
            self._publish_run(staging_root, request)
            try:
                published_receipt = self.read_receipt(result.receipt_artifact)
            except VerificationEvidenceError as exc:
                raise DockerVerifierError(
                    "published verifier receipt failed host validation"
                ) from exc
            if published_receipt != result.receipt:
                raise DockerVerifierError(
                    "published receipt does not match the verifier result"
                )
            return result

    def read_receipt(self, reference: ArtifactRef) -> VerificationReceipt:
        return FilesystemVerificationEvidenceStore(self.artifact_root).read_receipt(
            reference
        )

    def command(
        self,
        specification: CommandSpec,
        *,
        workspace: Path,
        cidfile: Path,
        execution_identity: VerifierExecutionIdentity,
        asset_view: Path | None = None,
    ) -> list[str]:
        return self._runtime().command(
            specification,
            workspace=workspace,
            cidfile=cidfile,
            execution_identity=execution_identity,
            asset_view=asset_view,
        )

    def _materialize_assets(
        self,
        request: BundleVerificationRequest,
        staging_directory: Path,
    ) -> Path | None:
        if request.profile.asset_bundle is None:
            return None
        if self.asset_store is None:
            raise DockerVerifierError(
                "verification profile requires an asset bundle store"
            )
        asset_view = staging_directory / "verifier-assets"
        try:
            FilesystemVerifierAssetBundleStore(self.asset_store).materialize(
                request.profile.asset_bundle,
                asset_view,
            )
        except VerifierAssetError as exc:
            raise DockerVerifierError(
                "verifier asset bundle failed isolated-view validation"
            ) from exc
        return asset_view

    def _runtime(self) -> DockerRuntime:
        return DockerRuntime(
            image=self.image,
            memory=self.memory,
            cpus=self.cpus,
            pids_limit=self.pids_limit,
            max_output_bytes=self.max_output_bytes,
            process=_TransportProcessPort(self),
            inspect_run=subprocess.run,
        )

    def _host_verifier(self) -> DockerHostVerifier:
        return DockerHostVerifier(
            runtime=self,
            workspace_hash=workspace_tree_hash,
            asset_hash=verifier_asset_tree_hash,
            validate_bindings=validate_final_verification_bindings,
        )

    # The following one-line delegates preserve historical private patch points
    # while assigning implementation ownership to focused collaborators.
    def _sandbox_prefix(self, cidfile: Path) -> list[str]:
        return self._runtime().sandbox_prefix(cidfile)

    def _execute_host_owned(
        self,
        request: BundleVerificationRequest,
        *,
        workspace: Path,
        staging_root: Path,
        asset_view: Path | None,
        staging_directory: Path,
    ) -> VerificationServiceResult:
        return self._host_verifier().execute(
            request,
            workspace=workspace,
            staging_root=staging_root,
            asset_view=asset_view,
            staging_directory=staging_directory,
            timeout_seconds=self.timeout_seconds,
        )

    def _execute_host_command(
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
        return self._host_verifier().execute_command(
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

    def _probe_executable(
        self,
        specification: CommandSpec,
        *,
        workspace: Path,
        asset_view: Path | None,
        cidfile: Path,
        execution_identity: VerifierExecutionIdentity,
        timeout_seconds: float,
    ) -> tuple[str | None, str | None, str | None]:
        return self._runtime().probe_executable(
            specification,
            workspace=workspace,
            asset_view=asset_view,
            cidfile=cidfile,
            execution_identity=execution_identity,
            timeout_seconds=timeout_seconds,
        )

    def _capture_command(
        self,
        specification: CommandSpec,
        *,
        workspace: Path,
        asset_view: Path | None,
        cidfile: Path,
        execution_identity: VerifierExecutionIdentity,
        timeout_seconds: float,
    ) -> _CommandCapture:
        return self._runtime().capture_command(
            specification,
            workspace=workspace,
            asset_view=asset_view,
            cidfile=cidfile,
            execution_identity=execution_identity,
            timeout_seconds=timeout_seconds,
        )

    def _prepare_bundle_view(
        self,
        reference: WorkspaceBundleRef,
        destination: Path,
    ) -> None:
        prepare_bundle_view(
            self.bundle_store,
            reference,
            destination,
            same_stable_file=_same_stable_file,
        )

    def _run_container(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return DockerProcessRunner(self.max_output_bytes).run(
            command,
            timeout_seconds=self.timeout_seconds,
        )

    @staticmethod
    def _parse_result(
        completed: subprocess.CompletedProcess[str],
        request: BundleVerificationRequest,
    ) -> VerificationServiceResult:
        return parse_legacy_result(
            completed,
            request,
            validate_bindings=validate_final_verification_bindings,
        )

    def _publish_run(
        self,
        staging_root: Path,
        request: BundleVerificationRequest,
    ) -> None:
        DockerEvidencePublisher(self.artifact_root).publish(
            staging_root,
            request,
            write_json=write_json_atomic,
            replace_path=os.replace,
            fsync_directory=_fsync_directory,
        )

    @staticmethod
    def _remove_container(cidfile: Path) -> None:
        remove_container(cidfile, run=subprocess.run)
