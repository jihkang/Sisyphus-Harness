from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import selectors
import signal
import subprocess
import threading
import time
from typing import Callable, Protocol

from ..contracts.codec import loads_strict_json
from ..contracts.verification import CommandSpec
from ..contracts.verification_service import VerifierExecutionIdentity


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


class DockerProcessPort(Protocol):
    def run(
        self,
        command: list[str],
        *,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]: ...

    def remove_container(self, cidfile: Path) -> None: ...


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
class DockerRuntime:
    image: str
    memory: str
    cpus: str
    pids_limit: int
    max_output_bytes: int
    process: DockerProcessPort
    inspect_run: Callable[..., subprocess.CompletedProcess[str]]

    def execution_identity(self) -> VerifierExecutionIdentity:
        if _is_sha256(self.image):
            image_id = self.image
        else:
            try:
                completed = self.inspect_run(
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
        command = self.sandbox_prefix(cidfile)
        command.extend(
            (
                "--workdir",
                "/workspace",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--mount",
                docker_bind_mount(workspace, "/workspace", readonly=False),
            )
        )
        if asset_view is not None:
            command.extend(
                (
                    "--mount",
                    docker_bind_mount(
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

    def sandbox_prefix(self, cidfile: Path) -> list[str]:
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

    def probe_executable(
        self,
        specification: CommandSpec,
        *,
        workspace: Path,
        asset_view: Path | None,
        cidfile: Path,
        execution_identity: VerifierExecutionIdentity,
        timeout_seconds: float,
    ) -> tuple[str | None, str | None, str | None]:
        command = self.sandbox_prefix(cidfile)
        command.extend(
            (
                "--workdir",
                "/workspace",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--mount",
                docker_bind_mount(workspace, "/workspace", readonly=True),
            )
        )
        if asset_view is not None:
            command.extend(
                (
                    "--mount",
                    docker_bind_mount(
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
            completed = self.process.run(command, timeout_seconds=timeout_seconds)
        except _DockerOutputLimitError as exc:
            self.process.remove_container(cidfile)
            raise DockerVerifierError(
                "verification executable probe exceeded output limit"
            ) from exc
        except OSError as exc:
            self.process.remove_container(cidfile)
            raise DockerVerifierError(
                "verification executable probe could not start"
            ) from exc
        if completed.returncode == 125:
            self.process.remove_container(cidfile)
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

    def capture_command(
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
            completed = self.process.run(command, timeout_seconds=timeout_seconds)
        except _DockerOutputLimitError as exc:
            self.process.remove_container(cidfile)
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
            self.process.remove_container(cidfile)
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
            self.process.remove_container(cidfile)
            raise DockerVerifierError("verifier command container could not start") from exc
        if completed.returncode == 125:
            self.process.remove_container(cidfile)
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


@dataclass(frozen=True, slots=True)
class DockerProcessRunner:
    max_output_bytes: int

    def run(
        self,
        command: list[str],
        *,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(  # nosec B603 - argv is constructed by DockerRuntime
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
            start_new_session=os.name != "nt",
        )
        try:
            if os.name == "nt":  # pragma: no cover - Windows pipe fallback
                stdout, stderr = self._collect_with_threads(
                    process,
                    timeout_seconds=timeout_seconds,
                )
            else:
                stdout, stderr = self._collect_with_selector(
                    process,
                    timeout_seconds=timeout_seconds,
                )
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

    def _collect_with_selector(
        self,
        process: subprocess.Popen[bytes],
        *,
        timeout_seconds: float,
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
        deadline = time.monotonic() + timeout_seconds
        total = 0
        try:
            while selector.get_map():
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    raise subprocess.TimeoutExpired(
                        process.args,
                        timeout_seconds,
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
                    timeout_seconds,
                    output=bytes(output[process.stdout.fileno()]),
                    stderr=bytes(output[process.stderr.fileno()]),
                )
            process.wait(timeout=remaining_time)
        finally:
            selector.close()
        return bytes(output[process.stdout.fileno()]), bytes(
            output[process.stderr.fileno()]
        )

    def _collect_with_threads(
        self,
        process: subprocess.Popen[bytes],
        *,
        timeout_seconds: float,
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
        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None and not exceeded.is_set():
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                with lock:
                    raise subprocess.TimeoutExpired(
                        process.args,
                        timeout_seconds,
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
                    timeout_seconds,
                    output=bytes(stdout),
                    stderr=bytes(stderr),
                )
        return bytes(stdout), bytes(stderr)


def remove_container(
    cidfile: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    try:
        if cidfile.stat().st_size > 128:
            return
        container_id = cidfile.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return
    if not _is_container_id(container_id):
        return
    try:
        run(
            ("docker", "rm", "--force", container_id),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


def docker_bind_mount(source: Path, destination: str, *, readonly: bool) -> str:
    source_field = f"src={source.resolve()}"
    if any(character in source_field for character in ("\x00", "\r", "\n")):
        raise DockerVerifierError("Docker bind source contains a control character")
    if "," in source_field or '"' in source_field:
        source_field = f'"{source_field.replace(chr(34), chr(34) * 2)}"'
    options = ["type=bind", source_field, f"dst={destination}"]
    if readonly:
        options.append("readonly")
    return ",".join(options)


def _is_container_id(value: str) -> bool:
    return 12 <= len(value) <= 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _duration_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


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


def _is_sha256(value: str) -> bool:
    return value.startswith("sha256:") and len(value) == 71 and all(
        character in "0123456789abcdef" for character in value[7:]
    )


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
