from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import threading
import time
from typing import BinaryIO
import uuid

from .contracts.artifacts import ArtifactRef
from .contracts.verification import (
    CommandResult,
    CommandSpec,
    VerificationReceipt,
    VerificationRequest,
)
from .workspace_state_adapters import GitWorkspaceStateAdapter
from .infra.verification_evidence import FilesystemVerificationEvidenceStore
from .ports.workspace_state import WorkspaceStatePort
from .receipts import write_json_atomic, write_text_atomic
from .workspace import contained_path


class VerificationError(RuntimeError):
    pass


_OUTPUT_DRAIN_GRACE_SECONDS = 0.5
_PROCESS_CLEANUP_SECONDS = 1.0
_PROCESS_POLL_SECONDS = 0.05
_USE_THREAD_CAPTURE = os.name == "nt"


class BoundedVerifier:
    def __init__(
        self,
        artifact_root: Path,
        *,
        workspace_state: WorkspaceStatePort | None = None,
        max_output_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        if (
            isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or max_output_bytes <= 0
        ):
            raise ValueError("verification output limit must be positive")
        self.artifact_root = artifact_root
        self.evidence_store = FilesystemVerificationEvidenceStore(artifact_root)
        self.workspace_state = workspace_state or GitWorkspaceStateAdapter()
        self.max_output_bytes = max_output_bytes

    def receipt_reference(self, run_id: str) -> ArtifactRef:
        return self.evidence_store.receipt_reference(run_id)

    def read_receipt(self, reference: ArtifactRef) -> VerificationReceipt:
        return self.evidence_store.read_receipt(reference)

    def verify(
        self,
        workspace: Path,
        commands: tuple[CommandSpec, ...],
        *,
        run_id: str | None = None,
        request_digest: str | None = None,
        deadline_monotonic: float | None = None,
        workspace_bundle_id: str | None = None,
        profile_digest: str | None = None,
        execution_identity_digest: str | None = None,
        verifier_asset_bundle_id: str | None = None,
    ) -> VerificationReceipt:
        if not commands:
            raise VerificationError("verification requires at least one command")
        names = [command.name for command in commands]
        if len(set(names)) != len(names):
            raise VerificationError("verification command names must be unique")
        if deadline_monotonic is not None and not math.isfinite(deadline_monotonic):
            raise VerificationError("verification deadline must be finite")
        service_bindings = (
            workspace_bundle_id,
            profile_digest,
            execution_identity_digest,
        )
        if any(value is not None for value in service_bindings) and not all(
            value is not None for value in service_bindings
        ):
            raise VerificationError(
                "service verification bindings must be supplied together"
            )
        if verifier_asset_bundle_id is not None and workspace_bundle_id is None:
            raise VerificationError(
                "verifier asset binding requires service verification bindings"
            )
        if workspace_bundle_id is not None and request_digest is None:
            raise VerificationError(
                "service verification binding requires request digest"
            )
        root = workspace.resolve()
        if not root.is_dir():
            raise VerificationError(f"verification workspace does not exist: {workspace}")
        baseline = self.workspace_state.snapshot(root)
        resolved_run_id = run_id or f"verify-{uuid.uuid4().hex}"
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", resolved_run_id)
            is None
            or resolved_run_id in {".", ".."}
        ):
            raise VerificationError("verification run ID contains unsafe characters")
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        run_dir = contained_path(
            self.artifact_root,
            resolved_run_id,
            require_relative=True,
        )
        if run_dir.exists():
            raise VerificationError(f"verification run already exists: {resolved_run_id}")
        run_dir.mkdir(parents=True)
        request = VerificationRequest(
            run_id=resolved_run_id,
            workspace=str(root),
            workspace_state_before=baseline.state_hash,
            commands=commands,
        )
        write_json_atomic(run_dir / "request.json", request.to_dict())
        started_at = _utc_now()
        results = tuple(
            self._run_command(
                root,
                run_dir,
                index,
                command,
                deadline_monotonic=deadline_monotonic,
            )
            for index, command in enumerate(commands)
        )
        final = self.workspace_state.snapshot(root)
        workspace_unchanged = baseline.state_hash == final.state_hash
        receipt = VerificationReceipt(
            run_id=resolved_run_id,
            workspace=str(root),
            worktree_commit_sha=baseline.commit_sha,
            started_at=started_at,
            finished_at=_utc_now(),
            passed=workspace_unchanged and all(result.passed for result in results),
            commands=results,
            workspace_state_before=baseline.state_hash,
            workspace_state_after=final.state_hash,
            workspace_unchanged=workspace_unchanged,
            request_digest=request_digest or request.request_digest,
            schema_version=(
                "sisyphus_harness.verification.v3"
                if workspace_bundle_id is not None
                else "sisyphus_harness.verification.v2"
            ),
            workspace_bundle_id=workspace_bundle_id,
            profile_digest=profile_digest,
            execution_identity_digest=execution_identity_digest,
            verifier_asset_bundle_id=verifier_asset_bundle_id,
        )
        write_json_atomic(run_dir / "receipt.json", receipt.to_dict())
        return receipt

    def _run_command(
        self,
        workspace: Path,
        run_dir: Path,
        index: int,
        command: CommandSpec,
        *,
        deadline_monotonic: float | None,
    ) -> CommandResult:
        before = self.workspace_state.snapshot(workspace)
        command_dir = run_dir / f"{index:02d}-{_safe_name(command.name)}"
        command_dir.mkdir()
        stdout_path = command_dir / "stdout.txt"
        stderr_path = command_dir / "stderr.txt"
        try:
            executable = _resolve_executable(workspace, command.argv[0])
            executable_path: str | None = str(executable)
            executable_sha256: str | None = f"sha256:{_sha256_file(executable)}"
        except VerificationError as exc:
            write_text_atomic(stdout_path, "")
            write_text_atomic(stderr_path, f"{exc}\n")
            after = self.workspace_state.snapshot(workspace)
            return CommandResult(
                name=command.name,
                argv=command.argv,
                criteria=command.criteria,
                passed=False,
                timed_out=False,
                exit_code=None,
                duration_ms=0,
                executable_path=None,
                executable_sha256=None,
                stdout_path=stdout_path.relative_to(run_dir).as_posix(),
                stderr_path=stderr_path.relative_to(run_dir).as_posix(),
                workspace_state_before=before.state_hash,
                workspace_state_after=after.state_hash,
                workspace_unchanged=before.state_hash == after.state_hash,
                failure_category="launch_error",
                error=str(exc),
            )
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        popen_kwargs: dict[str, object] = {
            "cwd": workspace,
            "env": environment,
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        started = time.monotonic()
        timeout_seconds = command.timeout_seconds
        if deadline_monotonic is not None:
            timeout_seconds = max(
                0.001,
                min(timeout_seconds, deadline_monotonic - started),
            )
        launch_error: str | None = None
        timed_out = False
        process_leaked = False
        output_limited = threading.Event()
        process: subprocess.Popen[bytes] | None = None
        output_budget = _OutputBudget(self.max_output_bytes)
        with stdout_path.open("wb") as stdout_handle, stderr_path.open(
            "wb"
        ) as stderr_handle:
            try:
                process = subprocess.Popen(
                    command.argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **popen_kwargs,
                )
            except OSError as exc:
                launch_error = (
                    f"failed to start verification command {command.name!r}: {exc}"
                )
                stderr_handle.write(f"{launch_error}\n".encode("utf-8"))
            if process is not None:
                assert process.stdout is not None
                assert process.stderr is not None
                if _USE_THREAD_CAPTURE:
                    timed_out, process_leaked = _capture_with_threads(
                        process,
                        stdout_handle,
                        stderr_handle,
                        output_budget,
                        output_limited,
                        timeout_seconds,
                    )
                else:
                    timed_out, process_leaked = _capture_with_selector(
                        process,
                        stdout_handle,
                        stderr_handle,
                        output_budget,
                        output_limited,
                        timeout_seconds,
                    )
            stdout_handle.flush()
            stderr_handle.flush()
            os.fsync(stdout_handle.fileno())
            os.fsync(stderr_handle.fileno())
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        exit_code = process.returncode if process is not None else None
        after = self.workspace_state.snapshot(workspace)
        workspace_unchanged = before.state_hash == after.state_hash
        passed = (
            launch_error is None
            and not timed_out
            and not output_limited.is_set()
            and not process_leaked
            and exit_code == 0
            and workspace_unchanged
        )
        failure_category = classify_command_failure(
            passed=passed,
            timed_out=timed_out,
            output_limited=output_limited.is_set(),
            process_leaked=process_leaked,
            launch_error=launch_error,
            workspace_unchanged=workspace_unchanged,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        return CommandResult(
            name=command.name,
            argv=command.argv,
            criteria=command.criteria,
            passed=passed,
            timed_out=timed_out,
            exit_code=exit_code,
            duration_ms=duration_ms,
            executable_path=executable_path,
            executable_sha256=executable_sha256,
            stdout_path=stdout_path.relative_to(run_dir).as_posix(),
            stderr_path=stderr_path.relative_to(run_dir).as_posix(),
            workspace_state_before=before.state_hash,
            workspace_state_after=after.state_hash,
            workspace_unchanged=workspace_unchanged,
            failure_category=failure_category,
            error=(
                f"verification output exceeded {self.max_output_bytes} bytes"
                if output_limited.is_set()
                else (
                    "verification command left descendant processes or output "
                    "pipes active after exit"
                    if process_leaked
                    else launch_error
                )
            ),
        )


def _resolve_executable(workspace: Path, raw: str) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    elif (
        len(candidate.parts) > 1
        or raw.startswith("./")
        or raw.startswith(".\\")
    ):
        resolved = (workspace / candidate).resolve()
    else:
        discovered = shutil.which(raw)
        if discovered is None:
            raise VerificationError(f"verification executable not found: {raw}")
        resolved = Path(discovered).resolve()
    if not resolved.is_file():
        raise VerificationError(f"verification executable is not a file: {resolved}")
    return resolved


def classify_command_failure(
    *,
    passed: bool,
    timed_out: bool,
    output_limited: bool,
    process_leaked: bool,
    launch_error: str | None,
    workspace_unchanged: bool,
    stdout_path: Path,
    stderr_path: Path,
) -> str | None:
    if passed:
        return None
    if launch_error is not None:
        return "launch_error"
    if output_limited:
        return "output_limit"
    if timed_out:
        return "timeout"
    if process_leaked:
        return "process_leak"
    if not workspace_unchanged:
        return "workspace_mutation"

    diagnostic = "\n".join(
        _read_diagnostic_prefix(path) for path in (stderr_path, stdout_path)
    )
    if re.search(
        r"\b(?:SyntaxError|IndentationError|TabError|NameError|UnboundLocalError|"
        r"ImportError|ModuleNotFoundError|AttributeError|TypeError|ValueError|"
        r"KeyError|IndexError|ZeroDivisionError|RuntimeError)\b",
        diagnostic,
    ):
        return "execution_error"
    if re.search(r"\bAssertionError\b|\bassertion failed\b", diagnostic, re.I):
        return "assertion_failure"
    return "command_failure"


def _read_diagnostic_prefix(path: Path, limit: int = 65_536) -> str:
    try:
        return path.read_bytes()[:limit].decode("utf-8", errors="replace")
    except OSError:
        return ""


class _OutputBudget:
    def __init__(self, limit: int) -> None:
        self.remaining = limit
        self.lock = threading.Lock()

    def take(self, content: bytes) -> tuple[bytes, bool]:
        with self.lock:
            accepted = content[: self.remaining]
            self.remaining -= len(accepted)
            return accepted, len(accepted) != len(content)


def _capture_with_selector(
    process: subprocess.Popen[bytes],
    stdout: BinaryIO,
    stderr: BinaryIO,
    budget: _OutputBudget,
    exceeded: threading.Event,
    timeout_seconds: float,
) -> tuple[bool, bool]:
    selector = selectors.DefaultSelector()
    streams = (
        (process.stdout, stdout),
        (process.stderr, stderr),
    )
    try:
        for source, destination in streams:
            assert source is not None
            os.set_blocking(source.fileno(), False)
            selector.register(source, selectors.EVENT_READ, destination)
    except BaseException:
        _kill_process_group(process)
        _wait_process(process, _PROCESS_CLEANUP_SECONDS)
        for key in tuple(selector.get_map().values()):
            _close_selector_stream(selector, key.fileobj)
        selector.close()
        raise

    deadline = time.monotonic() + timeout_seconds
    exit_observed_at: float | None = None
    timed_out = False
    process_leaked = False
    try:
        while True:
            now = time.monotonic()
            process_running = process.poll() is None
            if process_running and now >= deadline:
                timed_out = True
                _terminate_process_group(process)
                if not _wait_process(process, _PROCESS_CLEANUP_SECONDS):
                    process_leaked = True
                    _kill_process_group(process)
                    if not _wait_process(process, _PROCESS_CLEANUP_SECONDS):
                        return timed_out, process_leaked
                continue

            if not process_running:
                if exit_observed_at is None:
                    exit_observed_at = now
                    # Reaping the group leader does not imply that the process
                    # group is empty; descendants can outlive a successful parent.
                    if _process_group_alive(process.pid):
                        process_leaked = True
                        _kill_process_group(process)
                if not selector.get_map():
                    break
                # A descendant can escape the original group while retaining an
                # output descriptor. Never wait indefinitely for that pipe's EOF.
                if now - exit_observed_at >= _OUTPUT_DRAIN_GRACE_SECONDS:
                    process_leaked = True
                    _kill_process_group(process)
                    break

            wait_seconds = _PROCESS_POLL_SECONDS
            if process_running:
                wait_seconds = min(wait_seconds, max(0.0, deadline - now))
            elif exit_observed_at is not None:
                wait_seconds = min(
                    wait_seconds,
                    max(
                        0.0,
                        _OUTPUT_DRAIN_GRACE_SECONDS - (now - exit_observed_at),
                    ),
                )

            if not selector.get_map():
                _wait_process(process, wait_seconds)
                continue
            for key, _ in selector.select(wait_seconds):
                source = key.fileobj
                destination = key.data
                try:
                    chunk = os.read(source.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                except OSError:
                    chunk = b""
                if not chunk:
                    _close_selector_stream(selector, source)
                    continue
                accepted, over_limit = budget.take(chunk)
                if accepted:
                    destination.write(accepted)
                if over_limit and not exceeded.is_set():
                    exceeded.set()
                    _kill_process_group(process)
                    if not _wait_process(process, _PROCESS_CLEANUP_SECONDS):
                        process_leaked = True
                        return timed_out, process_leaked
    finally:
        if process.poll() is None:
            _kill_process_group(process)
            _wait_process(process, _PROCESS_CLEANUP_SECONDS)
        for key in tuple(selector.get_map().values()):
            _close_selector_stream(selector, key.fileobj)
        selector.close()
    return timed_out, process_leaked


def _close_selector_stream(
    selector: selectors.BaseSelector,
    source: BinaryIO,
) -> None:
    try:
        selector.unregister(source)
    except (KeyError, ValueError):
        pass
    try:
        source.close()
    except (OSError, ValueError):
        pass


def _capture_with_threads(
    process: subprocess.Popen[bytes],
    stdout: BinaryIO,
    stderr: BinaryIO,
    budget: _OutputBudget,
    exceeded: threading.Event,
    timeout_seconds: float,
) -> tuple[bool, bool]:
    assert process.stdout is not None
    assert process.stderr is not None
    readers = (
        threading.Thread(
            target=_capture_output,
            args=(process.stdout, stdout, budget, exceeded, process),
            daemon=True,
        ),
        threading.Thread(
            target=_capture_output,
            args=(process.stderr, stderr, budget, exceeded, process),
            daemon=True,
        ),
    )
    for reader in readers:
        reader.start()
    timed_out = not _wait_process(process, timeout_seconds)
    if timed_out:
        _terminate_process_group(process)
    process_leaked = process.poll() is None
    if process_leaked:
        _kill_process_group(process)
        _wait_process(process, _PROCESS_CLEANUP_SECONDS)

    drain_deadline = time.monotonic() + _OUTPUT_DRAIN_GRACE_SECONDS
    for reader in readers:
        reader.join(timeout=max(0.0, drain_deadline - time.monotonic()))
    if any(reader.is_alive() for reader in readers):
        # Platforms without selectable subprocess pipes still fail closed when a
        # descendant keeps an inherited output descriptor open.
        process_leaked = True
        _kill_process_group(process)
    return timed_out, process_leaked


def _capture_output(
    source: BinaryIO,
    destination: BinaryIO,
    budget: _OutputBudget,
    exceeded: threading.Event,
    process: subprocess.Popen[bytes],
) -> None:
    try:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            accepted, over_limit = budget.take(chunk)
            if accepted:
                destination.write(accepted)
            if over_limit and not exceeded.is_set():
                exceeded.set()
                _kill_process_group(process)
    except (OSError, ValueError):
        return
    finally:
        try:
            source.close()
        except OSError:
            pass


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "nt":
            if process.poll() is None:
                process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (PermissionError, ProcessLookupError):
        return


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        if process.poll() is not None:
            return
        process.terminate()
        if not _wait_process(process, _PROCESS_CLEANUP_SECONDS):
            process.kill()
            _wait_process(process, _PROCESS_CLEANUP_SECONDS)
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (PermissionError, ProcessLookupError):
        return
    if not _wait_process(process, _PROCESS_CLEANUP_SECONDS):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (PermissionError, ProcessLookupError):
            return
        _wait_process(process, _PROCESS_CLEANUP_SECONDS)


def _wait_process(process: subprocess.Popen[bytes], timeout: float) -> bool:
    if process.poll() is not None:
        return True
    try:
        process.wait(timeout=max(0.001, timeout))
    except subprocess.TimeoutExpired:
        return False
    return True


def _process_group_alive(process_group_id: int) -> bool:
    if os.name == "nt":
        return False
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return normalized or "command"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
