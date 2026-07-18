from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
import uuid

from .contracts.verification import CommandResult, CommandSpec, VerificationReceipt
from .receipts import write_json_atomic, write_text_atomic
from .workspace import contained_path, snapshot_workspace


class VerificationError(RuntimeError):
    pass


class BoundedVerifier:
    def __init__(self, artifact_root: Path) -> None:
        self.artifact_root = artifact_root

    def verify(
        self,
        workspace: Path,
        commands: tuple[CommandSpec, ...],
        *,
        run_id: str | None = None,
    ) -> VerificationReceipt:
        if not commands:
            raise VerificationError("verification requires at least one command")
        names = [command.name for command in commands]
        if len(set(names)) != len(names):
            raise VerificationError("verification command names must be unique")
        root = workspace.resolve()
        if not root.is_dir():
            raise VerificationError(f"verification workspace does not exist: {workspace}")
        baseline = snapshot_workspace(root)
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
        started_at = _utc_now()
        results = tuple(
            self._run_command(root, run_dir, index, command)
            for index, command in enumerate(commands)
        )
        final = snapshot_workspace(root)
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
        )
        write_json_atomic(run_dir / "receipt.json", receipt.to_dict())
        return receipt

    def _run_command(
        self,
        workspace: Path,
        run_dir: Path,
        index: int,
        command: CommandSpec,
    ) -> CommandResult:
        before = snapshot_workspace(workspace)
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
            after = snapshot_workspace(workspace)
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
        launch_error: str | None = None
        timed_out = False
        process: subprocess.Popen[bytes] | None = None
        with stdout_path.open("wb") as stdout_handle, stderr_path.open(
            "wb"
        ) as stderr_handle:
            try:
                process = subprocess.Popen(
                    command.argv,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    **popen_kwargs,
                )
            except OSError as exc:
                launch_error = (
                    f"failed to start verification command {command.name!r}: {exc}"
                )
                stderr_handle.write(f"{launch_error}\n".encode("utf-8"))
            if process is not None:
                try:
                    process.wait(timeout=command.timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _terminate_process_group(process)
                    process.wait()
            stdout_handle.flush()
            stderr_handle.flush()
            os.fsync(stdout_handle.fileno())
            os.fsync(stderr_handle.fileno())
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        exit_code = process.returncode if process is not None else None
        after = snapshot_workspace(workspace)
        workspace_unchanged = before.state_hash == after.state_hash
        passed = (
            launch_error is None
            and not timed_out
            and exit_code == 0
            and workspace_unchanged
        )
        failure_category = _failure_category(
            passed=passed,
            timed_out=timed_out,
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
            error=launch_error,
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


def _failure_category(
    *,
    passed: bool,
    timed_out: bool,
    launch_error: str | None,
    workspace_unchanged: bool,
    stdout_path: Path,
    stderr_path: Path,
) -> str | None:
    if passed:
        return None
    if launch_error is not None:
        return "launch_error"
    if timed_out:
        return "timeout"
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


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)


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
