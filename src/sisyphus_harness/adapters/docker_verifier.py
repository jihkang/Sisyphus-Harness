from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import threading
import time

from ..contracts.codec import loads_strict_json
from ..contracts.verification_service import (
    BundleVerificationRequest,
    VerificationServiceResult,
    VerifierExecutionIdentity,
)
from ..contracts.artifacts import ArtifactRef
from ..contracts.verification import (
    CommandResult,
    CommandSpec,
    VerificationReceipt,
    VerificationRequest,
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
from ..receipts import write_json_atomic, write_text_atomic
from ..verifier import classify_command_failure
from .receipt_observations import (
    VerificationBindingError,
    validate_final_verification_bindings,
)


class DockerVerifierError(RuntimeError):
    pass


class _DockerOutputLimitError(RuntimeError):
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"") -> None:
        super().__init__("Docker verifier output exceeded its configured limit")
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True, slots=True)
class _CommandCapture:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    output_limited: bool = False
    launch_error: str | None = None
    duration_ms: int = 0


_BUNDLE_REFERENCE_LIMIT = 64 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_EXECUTABLE_PROBE = """\
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys

raw = sys.argv[1]
candidate = Path(raw)
if candidate.is_absolute():
    resolved = candidate.resolve()
elif len(candidate.parts) > 1 or raw.startswith(("./", ".\\\\")):
    resolved = (Path("/workspace") / candidate).resolve()
else:
    discovered = shutil.which(raw)
    if discovered is None:
        print(json.dumps({"error": f"verification executable not found: {raw}"}))
        raise SystemExit(127)
    resolved = Path(discovered).resolve()

flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
try:
    descriptor = os.open(resolved, flags)
except OSError as exc:
    print(json.dumps({"error": f"verification executable is unavailable: {exc}"}))
    raise SystemExit(127)
try:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        print(json.dumps({"error": "verification executable is not a regular file"}))
        raise SystemExit(127)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    after = os.fstat(descriptor)
    fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        print(json.dumps({"error": "verification executable changed while hashing"}))
        raise SystemExit(127)
finally:
    os.close(descriptor)
print(json.dumps({"path": str(resolved), "sha256": "sha256:" + digest.hexdigest()}))
"""


@dataclass(frozen=True, slots=True)
class DockerVerifierTransport:
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
        if _is_sha256(self.image):
            image_id = self.image
        else:
            try:
                completed = subprocess.run(
                    (
                        "docker",
                        "image",
                        "inspect",
                        "--format",
                        "{{.Id}}",
                        self.image,
                    ),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise DockerVerifierError(
                    "verifier image identity could not be resolved"
                ) from exc
            lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
            if (
                completed.returncode != 0
                or len(lines) != 1
                or not _is_sha256(lines[0])
            ):
                raise DockerVerifierError(
                    "verifier image identity could not be resolved"
                )
            image_id = lines[0]
        return VerifierExecutionIdentity(
            runtime="docker",
            image_reference=self.image,
            image_id=image_id,
        )

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
            asset_view: Path | None = None
            if request.profile.asset_bundle is not None:
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
        if type(specification) is not CommandSpec:
            raise TypeError("Docker verifier command requires an exact CommandSpec")
        command = self._sandbox_prefix(cidfile)
        command.extend(
            (
                "--workdir",
                "/workspace",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--mount",
                _docker_bind_mount(workspace, "/workspace", readonly=False),
            )
        )
        if asset_view is not None:
            command.extend(
                (
                    "--mount",
                    _docker_bind_mount(
                        asset_view,
                        "/verifier-assets",
                        readonly=True,
                    ),
                )
            )
        command.extend(
            (
                "--entrypoint",
                specification.argv[0],
                execution_identity.image_id,
                *specification.argv[1:],
            )
        )
        return command

    def _sandbox_prefix(self, cidfile: Path) -> list[str]:
        user = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "65532:65532"
        uid, gid = user.split(":")
        return [
            "docker",
            "run",
            "--rm",
            "--cidfile",
            str(cidfile.resolve()),
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            self.memory,
            "--cpus",
            self.cpus,
            "--user",
            user,
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size=128m,uid={uid},gid={gid}",  # nosec B108
        ]

    def _execute_host_owned(
        self,
        request: BundleVerificationRequest,
        *,
        workspace: Path,
        staging_root: Path,
        asset_view: Path | None,
        staging_directory: Path,
    ) -> VerificationServiceResult:
        baseline = workspace_tree_hash(workspace)
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
            asset_tree = verifier_asset_tree_hash(asset_view)
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
        started_at = _utc_now()
        deadline = time.monotonic() + self.timeout_seconds
        command_results: list[CommandResult] = []
        for index, specification in enumerate(request.profile.commands):
            command_results.append(
                self._execute_host_command(
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
            )

        final_state = workspace_tree_hash(workspace)
        workspace_unchanged = baseline == final_state
        receipt = VerificationReceipt(
            run_id=request.run_id,
            workspace="/workspace",
            worktree_commit_sha=request.workspace_bundle.source_commit_sha,
            started_at=started_at,
            finished_at=_utc_now(),
            passed=workspace_unchanged
            and all(result.passed for result in command_results),
            commands=tuple(command_results),
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
            validate_final_verification_bindings(request, result)
        except VerificationBindingError as exc:
            raise DockerVerifierError(str(exc)) from exc
        return result

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
        before = workspace_tree_hash(workspace)
        command_directory = run_directory / f"{index:02d}-{_safe_name(specification.name)}"
        command_directory.mkdir()
        stdout_path = command_directory / "stdout.txt"
        stderr_path = command_directory / "stderr.txt"
        remaining = deadline - time.monotonic()
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
                    self._probe_executable(
                        specification,
                        workspace=workspace,
                        asset_view=asset_view,
                        cidfile=probe_cidfile,
                        execution_identity=request.execution_identity,
                        timeout_seconds=min(remaining, 30.0),
                    )
                )
            except subprocess.TimeoutExpired:
                self._remove_container(probe_cidfile)
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
                    capture = self._capture_command(
                        specification,
                        workspace=workspace,
                        asset_view=asset_view,
                        cidfile=command_cidfile,
                        execution_identity=request.execution_identity,
                        timeout_seconds=min(
                            specification.timeout_seconds,
                            max(0.001, deadline - time.monotonic()),
                        ),
                    )

        write_text_atomic(stdout_path, capture.stdout)
        write_text_atomic(stderr_path, capture.stderr)
        after = workspace_tree_hash(workspace)
        workspace_unchanged = before == after
        if asset_view is not None:
            asset_tree_after = verifier_asset_tree_hash(asset_view)
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
            error = f"verification output exceeded {self.max_output_bytes} bytes"
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
        command = self._sandbox_prefix(cidfile)
        command.extend(
            (
                "--workdir",
                "/workspace",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--mount",
                _docker_bind_mount(workspace, "/workspace", readonly=True),
            )
        )
        if asset_view is not None:
            command.extend(
                (
                    "--mount",
                    _docker_bind_mount(
                        asset_view,
                        "/verifier-assets",
                        readonly=True,
                    ),
                )
            )
        command.extend(
            (
                "--entrypoint",
                "python",
                execution_identity.image_id,
                "-I",
                "-S",
                "-c",
                _EXECUTABLE_PROBE,
                specification.argv[0],
            )
        )
        try:
            completed = replace(
                self,
                timeout_seconds=min(self.timeout_seconds, timeout_seconds),
            )._run_container(command)
        except _DockerOutputLimitError as exc:
            self._remove_container(cidfile)
            raise DockerVerifierError(
                "verification executable probe exceeded output limit"
            ) from exc
        except OSError as exc:
            self._remove_container(cidfile)
            raise DockerVerifierError(
                "verification executable probe could not start"
            ) from exc
        if completed.returncode == 125:
            self._remove_container(cidfile)
            raise DockerVerifierError(
                completed.stderr.strip()[-2000:]
                or "Docker could not start the executable probe"
            )
        if completed.returncode != 0:
            detail = _probe_error(completed.stdout, completed.stderr)
            return None, None, detail
        try:
            raw = loads_strict_json(
                completed.stdout.strip(),
                label="verification executable probe",
            )
        except ValueError as exc:
            raise DockerVerifierError(str(exc)) from exc
        if not isinstance(raw, dict) or set(raw) != {"path", "sha256"}:
            raise DockerVerifierError("verification executable probe result is invalid")
        path = raw["path"]
        digest = raw["sha256"]
        if not isinstance(path, str) or not path or "\0" in path:
            raise DockerVerifierError("verification executable probe path is invalid")
        if not isinstance(digest, str) or not _is_sha256(digest):
            raise DockerVerifierError("verification executable probe digest is invalid")
        return path, digest, None

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
        command = self.command(
            specification,
            workspace=workspace,
            asset_view=asset_view,
            cidfile=cidfile,
            execution_identity=execution_identity,
        )
        started = time.monotonic()
        try:
            completed = replace(
                self,
                timeout_seconds=min(self.timeout_seconds, timeout_seconds),
            )._run_container(command)
        except _DockerOutputLimitError as exc:
            self._remove_container(cidfile)
            return _CommandCapture(
                returncode=None,
                stdout=_captured_text(exc.stdout),
                stderr=_append_diagnostic(
                    _captured_text(exc.stderr),
                    "verification command output exceeded limit",
                ),
                output_limited=True,
                duration_ms=_duration_ms(started),
            )
        except subprocess.TimeoutExpired as exc:
            self._remove_container(cidfile)
            return _CommandCapture(
                returncode=None,
                stdout=_captured_text(exc.output),
                stderr=_append_diagnostic(
                    _captured_text(exc.stderr),
                    "verification command timed out",
                ),
                timed_out=True,
                duration_ms=_duration_ms(started),
            )
        except OSError as exc:
            self._remove_container(cidfile)
            raise DockerVerifierError("verifier command container could not start") from exc
        if completed.returncode == 125:
            self._remove_container(cidfile)
            raise DockerVerifierError(
                completed.stderr.strip()[-2000:]
                or "Docker could not start the verifier command container"
            )
        launch_error = None
        returncode: int | None = completed.returncode
        if completed.returncode in {126, 127}:
            launch_error = (
                completed.stderr.strip()[-2000:]
                or f"verification executable could not start: {specification.argv[0]}"
            )
            returncode = None
        return _CommandCapture(
            returncode=returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            launch_error=launch_error,
            duration_ms=_duration_ms(started),
        )

    def _prepare_bundle_view(
        self,
        reference: WorkspaceBundleRef,
        destination: Path,
    ) -> None:
        try:
            directory_before = os.stat(self.bundle_store, follow_symlinks=False)
        except OSError as exc:
            raise DockerVerifierError(
                "workspace bundle store is not a regular directory"
            ) from exc
        if not stat.S_ISDIR(directory_before.st_mode):
            raise DockerVerifierError(
                "workspace bundle store is not a regular directory"
            )
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            directory_descriptor = os.open(self.bundle_store, flags)
        except OSError as exc:
            raise DockerVerifierError(
                "workspace bundle store is not a regular directory"
            ) from exc
        directory_opened = os.fstat(directory_descriptor)
        if not _same_stable_file(directory_before, directory_opened):
            os.close(directory_descriptor)
            raise DockerVerifierError(
                "workspace bundle store changed while being opened"
            )

        digest = reference.archive_sha256.removeprefix("sha256:")
        archive_name = f"{digest}.tar"
        reference_name = f"{digest}.json"
        try:
            destination.mkdir(mode=0o700)
            _copy_stable_regular_file(
                directory_descriptor,
                archive_name,
                destination / archive_name,
                max_bytes=reference.size_bytes,
                expected_size=reference.size_bytes,
                expected_sha256=digest,
            )
            reference_bytes = _copy_stable_regular_file(
                directory_descriptor,
                reference_name,
                destination / reference_name,
                max_bytes=_BUNDLE_REFERENCE_LIMIT,
            )
            directory_after = os.stat(self.bundle_store, follow_symlinks=False)
            if not _same_stable_file(directory_opened, directory_after):
                raise ValueError("workspace bundle store changed while being copied")
        except (OSError, ValueError) as exc:
            raise DockerVerifierError(
                "workspace bundle failed isolated-view validation"
            ) from exc
        finally:
            os.close(directory_descriptor)

        try:
            stored = WorkspaceBundleRef.from_dict(
                loads_strict_json(
                    reference_bytes.decode("utf-8"),
                    label="workspace bundle reference",
                )
            )
        except (UnicodeDecodeError, ValueError) as exc:
            raise DockerVerifierError(
                "workspace bundle reference failed host validation"
            ) from exc
        if stored != reference:
            raise DockerVerifierError(
                "workspace bundle reference does not match the request"
            )
        for path in destination.iterdir():
            path.chmod(0o444)
        destination.chmod(0o555)

    def _run_container(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(  # nosec B603 - argv is constructed above
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
            start_new_session=os.name != "nt",
        )
        try:
            if os.name == "nt":  # pragma: no cover - Windows pipe fallback
                stdout, stderr = self._collect_output_with_threads(process)
            else:
                stdout, stderr = self._collect_output_with_selector(process)
            return subprocess.CompletedProcess(
                command,
                process.returncode,
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace"),
            )
        except BaseException:
            _kill_process(process)
            raise
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()

    def _collect_output_with_selector(
        self,
        process: subprocess.Popen[bytes],
    ) -> tuple[bytes, bytes]:
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("Docker verifier output pipes are unavailable")
        output = {
            process.stdout.fileno(): bytearray(),
            process.stderr.fileno(): bytearray(),
        }
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        selector.register(process.stderr, selectors.EVENT_READ)
        deadline = time.monotonic() + self.timeout_seconds
        total = 0
        try:
            while selector.get_map():
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    raise subprocess.TimeoutExpired(
                        process.args,
                        self.timeout_seconds,
                        output=bytes(output[process.stdout.fileno()]),
                        stderr=bytes(output[process.stderr.fileno()]),
                    )
                events = selector.select(timeout=min(remaining_time, 0.1))
                for key, _ in events:
                    remaining_bytes = self.max_output_bytes - total
                    try:
                        chunk = os.read(
                            key.fd,
                            min(_READ_CHUNK_BYTES, remaining_bytes + 1),
                        )
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if len(chunk) > remaining_bytes:
                        output[key.fd].extend(chunk[:remaining_bytes])
                        raise _DockerOutputLimitError(
                            bytes(output[process.stdout.fileno()]),
                            bytes(output[process.stderr.fileno()]),
                        )
                    output[key.fd].extend(chunk)
                    total += len(chunk)
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                raise subprocess.TimeoutExpired(
                    process.args,
                    self.timeout_seconds,
                    output=bytes(output[process.stdout.fileno()]),
                    stderr=bytes(output[process.stderr.fileno()]),
                )
            process.wait(timeout=remaining_time)
        finally:
            selector.close()
        return bytes(output[process.stdout.fileno()]), bytes(
            output[process.stderr.fileno()]
        )

    def _collect_output_with_threads(
        self,
        process: subprocess.Popen[bytes],
    ) -> tuple[bytes, bytes]:  # pragma: no cover - Windows pipe fallback
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("Docker verifier output pipes are unavailable")
        stdout = bytearray()
        stderr = bytearray()
        lock = threading.Lock()
        exceeded = threading.Event()
        total = 0

        def drain(stream: object, target: bytearray) -> None:
            nonlocal total
            while not exceeded.is_set():
                chunk = stream.read(_READ_CHUNK_BYTES)  # type: ignore[attr-defined]
                if not chunk:
                    return
                with lock:
                    remaining = self.max_output_bytes - total
                    target.extend(chunk[:remaining])
                    total += min(len(chunk), remaining)
                    if len(chunk) > remaining:
                        exceeded.set()
                        return

        threads = (
            threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
        )
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + self.timeout_seconds
        while process.poll() is None and not exceeded.is_set():
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                with lock:
                    raise subprocess.TimeoutExpired(
                        process.args,
                        self.timeout_seconds,
                        output=bytes(stdout),
                        stderr=bytes(stderr),
                    )
            exceeded.wait(min(remaining_time, 0.01))
        if exceeded.is_set():
            with lock:
                raise _DockerOutputLimitError(bytes(stdout), bytes(stderr))
        for thread in threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))
        if exceeded.is_set():
            with lock:
                raise _DockerOutputLimitError(bytes(stdout), bytes(stderr))
        if any(thread.is_alive() for thread in threads):
            with lock:
                raise subprocess.TimeoutExpired(
                    process.args,
                    self.timeout_seconds,
                    output=bytes(stdout),
                    stderr=bytes(stderr),
                )
        return bytes(stdout), bytes(stderr)

    @staticmethod
    def _parse_result(
        completed: subprocess.CompletedProcess[str],
        request: BundleVerificationRequest,
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
            validate_final_verification_bindings(request, result)
        except VerificationBindingError as exc:
            raise DockerVerifierError(str(exc)) from exc
        return result

    def _publish_run(
        self,
        staging_root: Path,
        request: BundleVerificationRequest,
    ) -> None:
        source = staging_root / request.run_id
        destination = self.artifact_root / request.run_id
        if source.is_symlink() or not source.is_dir():
            raise DockerVerifierError("verifier did not create a regular run directory")
        lock_path = self.artifact_root / f".{request.run_id}.publish.lock"
        request_path = (
            self.artifact_root / "service-requests" / f"{request.run_id}.json"
        )
        request_written = False
        try:
            lock_descriptor = os.open(
                lock_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError as exc:
            raise DockerVerifierError("verification run is already being published") from exc
        try:
            if destination.exists() or destination.is_symlink():
                raise DockerVerifierError("verification run already exists")
            if request_path.exists() or request_path.is_symlink():
                raise DockerVerifierError(
                    "verification service request record already exists"
                )
            write_json_atomic(request_path, request.to_dict())
            request_written = True
            os.replace(source, destination)
            _fsync_directory(self.artifact_root)
        except DockerVerifierError:
            raise
        except OSError as exc:
            if request_written and not destination.exists():
                _remove_uncommitted_request(request_path)
            raise DockerVerifierError("verification run could not be published") from exc
        finally:
            os.close(lock_descriptor)
            lock_path.unlink(missing_ok=True)
            _fsync_directory(self.artifact_root)

    @staticmethod
    def _remove_container(cidfile: Path) -> None:
        try:
            if cidfile.stat().st_size > 128:
                return
            container_id = cidfile.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            return
        if not _is_container_id(container_id):
            return
        try:
            subprocess.run(
                ("docker", "rm", "--force", container_id),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return


def _is_container_id(value: str) -> bool:
    return 12 <= len(value) <= 64 and all(character in "0123456789abcdef" for character in value)


def _duration_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _remove_uncommitted_request(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        _fsync_directory(path.parent)
    except OSError:
        return


def _captured_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _append_diagnostic(content: str, diagnostic: str) -> str:
    separator = "" if not content or content.endswith("\n") else "\n"
    return f"{content}{separator}{diagnostic}\n"


def _probe_error(stdout: str, stderr: str) -> str:
    content = stdout.strip()
    if content:
        try:
            raw = loads_strict_json(content, label="verification executable probe error")
        except ValueError:
            raw = None
        if isinstance(raw, dict) and set(raw) == {"error"}:
            error = raw["error"]
            if isinstance(error, str) and error and "\0" not in error:
                return error[:2000]
    return stderr.strip()[-2000:] or "verification executable could not be resolved"


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return normalized or "command"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _is_sha256(value: str) -> bool:
    return value.startswith("sha256:") and len(value) == 71 and all(
        character in "0123456789abcdef" for character in value[7:]
    )


def _docker_bind_mount(source: Path, destination: str, *, readonly: bool) -> str:
    source_field = f"src={source.resolve()}"
    if any(character in source_field for character in ("\x00", "\r", "\n")):
        raise DockerVerifierError("Docker bind source contains a control character")
    if "," in source_field or '"' in source_field:
        source_field = f'"{source_field.replace(chr(34), chr(34) * 2)}"'
    options = ["type=bind", source_field, f"dst={destination}"]
    if readonly:
        options.append("readonly")
    return ",".join(options)


def _copy_stable_regular_file(
    directory_descriptor: int,
    name: str,
    destination: Path,
    *,
    max_bytes: int,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> bytes:
    path_before = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    if not stat.S_ISREG(path_before.st_mode):
        raise ValueError("workspace bundle object is not a regular file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    captured = bytearray()
    digest = hashlib.sha256()
    copied = 0
    try:
        before = os.fstat(descriptor)
        if not _same_stable_file(path_before, before):
            raise ValueError("workspace bundle object changed while being opened")
        if before.st_size > max_bytes:
            raise ValueError("workspace bundle object exceeds its declared limit")
        if expected_size is not None and before.st_size != expected_size:
            raise ValueError("workspace bundle archive size mismatch")
        with destination.open("xb") as output:
            while True:
                chunk = os.read(
                    descriptor,
                    min(_READ_CHUNK_BYTES, max_bytes - copied + 1),
                )
                if not chunk:
                    break
                copied += len(chunk)
                if copied > max_bytes:
                    raise ValueError("workspace bundle object exceeds its declared limit")
                digest.update(chunk)
                output.write(chunk)
                if expected_sha256 is None:
                    captured.extend(chunk)
            output.flush()
            os.fsync(output.fileno())
        after = os.fstat(descriptor)
        current = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if not _same_stable_file(before, after) or not _same_stable_file(
            before,
            current,
        ):
            raise ValueError("workspace bundle object changed while being copied")
        if expected_size is not None and copied != expected_size:
            raise ValueError("workspace bundle archive size mismatch")
        if expected_sha256 is not None and digest.hexdigest() != expected_sha256:
            raise ValueError("workspace bundle archive digest mismatch")
        return bytes(captured)
    finally:
        os.close(descriptor)


def _same_stable_file(before: os.stat_result, after: os.stat_result) -> bool:
    fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    return all(getattr(before, field) == getattr(after, field) for field in fields)


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
