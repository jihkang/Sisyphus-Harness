from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
import os
from pathlib import Path
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
)
from ..contracts.artifacts import ArtifactRef
from ..contracts.verification import VerificationReceipt
from ..contracts.workspace import WorkspaceBundleRef
from ..infra.verification_evidence import (
    FilesystemVerificationEvidenceStore,
    VerificationEvidenceError,
)
from ..receipts import write_json_atomic


class DockerVerifierError(RuntimeError):
    pass


class _DockerOutputLimitError(RuntimeError):
    pass


_BUNDLE_REFERENCE_LIMIT = 64 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class DockerVerifierTransport:
    bundle_store: Path
    artifact_root: Path
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
            request_path = staging_directory / "request.json"
            cidfile = staging_directory / "container.cid"
            write_json_atomic(request_path, request.to_dict())
            command = self.command(
                request_path,
                staging_root=staging_root,
                bundle_view=bundle_view,
                cidfile=cidfile,
            )
            try:
                completed = self._run_container(command)
            except _DockerOutputLimitError as exc:
                self._remove_container(cidfile)
                raise DockerVerifierError(
                    "verifier container output exceeded limit"
                ) from exc
            except (OSError, subprocess.TimeoutExpired) as exc:
                self._remove_container(cidfile)
                raise DockerVerifierError("verifier container execution failed") from exc
            result = self._parse_result(completed, request)
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
        request_path: Path,
        *,
        staging_root: Path,
        bundle_view: Path,
        cidfile: Path,
    ) -> list[str]:
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
            "--tmpfs",
            f"/work:rw,exec,nosuid,nodev,size=512m,uid={uid},gid={gid}",
            "--mount",
            _docker_bind_mount(bundle_view, "/bundles", readonly=True),
            "--mount",
            _docker_bind_mount(staging_root, "/artifacts", readonly=False),
            "--mount",
            _docker_bind_mount(request_path, "/request.json", readonly=True),
            self.image,
            "--request",
            "/request.json",
            "--bundle-store",
            "/bundles",
            "--artifact-root",
            "/artifacts",
            "--work-root",
            "/work",
        ]

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
        except (_DockerOutputLimitError, subprocess.TimeoutExpired):
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
                    raise subprocess.TimeoutExpired(process.args, self.timeout_seconds)
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
                        raise _DockerOutputLimitError
                    output[key.fd].extend(chunk)
                    total += len(chunk)
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                raise subprocess.TimeoutExpired(process.args, self.timeout_seconds)
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
                raise subprocess.TimeoutExpired(process.args, self.timeout_seconds)
            exceeded.wait(min(remaining_time, 0.01))
        if exceeded.is_set():
            raise _DockerOutputLimitError
        for thread in threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))
        if exceeded.is_set():
            raise _DockerOutputLimitError
        if any(thread.is_alive() for thread in threads):
            raise subprocess.TimeoutExpired(process.args, self.timeout_seconds)
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
        if result.receipt.run_id != request.run_id:
            raise DockerVerifierError("verifier result is bound to a different run")
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
            os.replace(source, destination)
            _fsync_directory(self.artifact_root)
            write_json_atomic(
                self.artifact_root
                / "service-requests"
                / f"{request.run_id}.json",
                request.to_dict(),
            )
        except DockerVerifierError:
            raise
        except OSError as exc:
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
