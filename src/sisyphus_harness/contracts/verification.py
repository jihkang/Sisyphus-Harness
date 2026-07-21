from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import PurePosixPath
import re

from .codec import WireModel, sha256_digest, strict_object


_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_FAILURE_CATEGORIES = {
    "assertion_failure",
    "command_failure",
    "execution_error",
    "launch_error",
    "output_limit",
    "process_leak",
    "timeout",
    "workspace_mutation",
}


@dataclass(frozen=True, slots=True)
class CommandSpec(WireModel):
    name: str
    argv: tuple[str, ...]
    timeout_seconds: float
    criteria: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("verification command name must be non-empty")
        if type(self.argv) is not tuple:
            raise ValueError("verification command argv must be an immutable tuple")
        if not self.argv or any(
            not isinstance(item, str) or not item.strip() or "\0" in item
            for item in self.argv
        ):
            raise ValueError(f"verification command {self.name!r} requires non-empty argv")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError(f"verification command {self.name!r} requires a positive timeout")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))
        if type(self.criteria) is not tuple:
            raise ValueError("verification command criteria must be an immutable tuple")
        if (
            not self.criteria
            or any(
                not isinstance(item, str) or not item.strip()
                for item in self.criteria
            )
            or len(set(self.criteria)) != len(self.criteria)
        ):
            raise ValueError(
                f"verification command {self.name!r} requires unique acceptance criteria"
            )

    @classmethod
    def from_dict(cls, raw: object) -> CommandSpec:
        raw = strict_object(
            raw,
            required={"name", "argv", "timeout_seconds", "criteria"},
            label="verification command",
        )
        timeout = raw["timeout_seconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("verification command timeout must be numeric")
        return cls(
            name=_string(raw["name"], "verification command name"),
            argv=_string_tuple(raw["argv"], "verification command argv"),
            timeout_seconds=float(timeout),
            criteria=_string_tuple(raw["criteria"], "verification command criteria"),
        )


@dataclass(frozen=True, slots=True)
class CommandResult(WireModel):
    name: str
    argv: tuple[str, ...]
    criteria: tuple[str, ...]
    passed: bool
    timed_out: bool
    exit_code: int | None
    duration_ms: int
    executable_path: str | None
    executable_sha256: str | None
    stdout_path: str
    stderr_path: str
    workspace_state_before: str
    workspace_state_after: str
    workspace_unchanged: bool
    failure_category: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        _string(self.name, "verification command result name")
        if type(self.argv) is not tuple:
            raise ValueError(
                "verification command result argv must be an immutable tuple"
            )
        if not self.argv or any(
            not isinstance(item, str) or not item.strip() or "\0" in item
            for item in self.argv
        ):
            raise ValueError("verification command result argv is invalid")
        if type(self.criteria) is not tuple:
            raise ValueError(
                "verification command result criteria must be an immutable tuple"
            )
        if any(not isinstance(item, str) or not item.strip() for item in self.criteria):
            raise ValueError("verification command result criteria is invalid")
        if not isinstance(self.passed, bool) or not isinstance(self.timed_out, bool):
            raise ValueError("verification command result status flags must be boolean")
        if self.exit_code is not None and (
            isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)
        ):
            raise ValueError("verification command result exit_code is invalid")
        if (
            isinstance(self.duration_ms, bool)
            or not isinstance(self.duration_ms, int)
            or self.duration_ms < 0
        ):
            raise ValueError("verification command result duration must be non-negative")
        if self.executable_path is not None:
            _string(self.executable_path, "verification executable path")
        if self.executable_sha256 is not None and (
            not isinstance(self.executable_sha256, str)
            or _SHA256.fullmatch(self.executable_sha256) is None
        ):
            raise ValueError("verification executable digest must be SHA-256")
        _relative_path(self.stdout_path, "verification stdout path")
        _relative_path(self.stderr_path, "verification stderr path")
        _string(self.workspace_state_before, "verification command state before")
        _string(self.workspace_state_after, "verification command state after")
        if not isinstance(self.workspace_unchanged, bool):
            raise ValueError(
                "verification command result workspace_unchanged must be boolean"
            )
        if self.workspace_unchanged != (
            self.workspace_state_before == self.workspace_state_after
        ):
            raise ValueError("verification command workspace state claim is inconsistent")
        if (
            self.failure_category is not None
            and self.failure_category not in _FAILURE_CATEGORIES
        ):
            raise ValueError("verification failure category is unsupported")
        if self.error is not None:
            _string(self.error, "verification command error")
        if self.passed and (
            self.timed_out
            or self.exit_code != 0
            or not self.workspace_unchanged
            or self.failure_category is not None
            or self.error is not None
        ):
            raise ValueError("passing verification command result is inconsistent")
        if not self.passed and self.failure_category is None:
            raise ValueError("failed verification command requires a failure category")

    @classmethod
    def from_dict(cls, raw: object) -> CommandResult:
        fields = {
            "name",
            "argv",
            "criteria",
            "passed",
            "timed_out",
            "exit_code",
            "duration_ms",
            "executable_path",
            "executable_sha256",
            "stdout_path",
            "stderr_path",
            "workspace_state_before",
            "workspace_state_after",
            "workspace_unchanged",
            "failure_category",
            "error",
        }
        raw = strict_object(raw, required=fields, label="verification command result")
        exit_code = raw["exit_code"]
        if exit_code is not None and (
            isinstance(exit_code, bool) or not isinstance(exit_code, int)
        ):
            raise ValueError("verification command result exit_code is invalid")
        duration_ms = raw["duration_ms"]
        if (
            isinstance(duration_ms, bool)
            or not isinstance(duration_ms, int)
            or duration_ms < 0
        ):
            raise ValueError("verification command result duration must be non-negative")
        executable_sha256 = _optional_string(
            raw["executable_sha256"],
            "verification executable digest",
        )
        if executable_sha256 is not None and _SHA256.fullmatch(executable_sha256) is None:
            raise ValueError("verification executable digest must be SHA-256")
        failure_category = _optional_string(
            raw["failure_category"],
            "verification failure category",
        )
        if (
            failure_category is not None
            and failure_category not in _FAILURE_CATEGORIES
        ):
            raise ValueError("verification failure category is unsupported")
        result = cls(
            name=_string(raw["name"], "verification command result name"),
            argv=_string_tuple(raw["argv"], "verification command result argv"),
            criteria=_string_tuple(
                raw["criteria"],
                "verification command result criteria",
            ),
            passed=_bool(raw["passed"], "verification command result passed"),
            timed_out=_bool(
                raw["timed_out"],
                "verification command result timed_out",
            ),
            exit_code=exit_code,
            duration_ms=duration_ms,
            executable_path=_optional_string(
                raw["executable_path"],
                "verification executable path",
            ),
            executable_sha256=executable_sha256,
            stdout_path=_relative_path(raw["stdout_path"], "verification stdout path"),
            stderr_path=_relative_path(raw["stderr_path"], "verification stderr path"),
            workspace_state_before=_string(
                raw["workspace_state_before"],
                "verification command state before",
            ),
            workspace_state_after=_string(
                raw["workspace_state_after"],
                "verification command state after",
            ),
            workspace_unchanged=_bool(
                raw["workspace_unchanged"],
                "verification command workspace_unchanged",
            ),
            failure_category=failure_category,
            error=_optional_string(raw["error"], "verification command error"),
        )
        if result.passed and (
            result.timed_out
            or result.exit_code != 0
            or not result.workspace_unchanged
            or result.failure_category is not None
            or result.error is not None
        ):
            raise ValueError("passing verification command result is inconsistent")
        if not result.passed and result.failure_category is None:
            raise ValueError("failed verification command requires a failure category")
        return result


@dataclass(frozen=True, slots=True)
class VerificationRequest(WireModel):
    run_id: str
    workspace: str
    workspace_state_before: str
    commands: tuple[CommandSpec, ...]
    schema_version: str = "sisyphus_harness.verification_request.v1"

    def __post_init__(self) -> None:
        _validate_run_id(self.run_id)
        _string(self.workspace, "verification request workspace")
        _string(
            self.workspace_state_before,
            "verification request workspace state",
        )
        if type(self.commands) is not tuple:
            raise ValueError("verification request commands must be an immutable tuple")
        if not self.commands or any(
            not isinstance(command, CommandSpec) for command in self.commands
        ):
            raise ValueError("verification request requires commands")
        if len({command.name for command in self.commands}) != len(self.commands):
            raise ValueError("verification request command names must be unique")
        if self.schema_version != "sisyphus_harness.verification_request.v1":
            raise ValueError("unsupported verification request schema")

    @property
    def request_digest(self) -> str:
        return sha256_digest(WireModel.to_dict(self))

    def to_dict(self) -> dict[str, object]:
        payload = WireModel.to_dict(self)
        payload["request_digest"] = self.request_digest
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> VerificationRequest:
        raw = strict_object(
            raw,
            required={
                "run_id",
                "workspace",
                "workspace_state_before",
                "commands",
                "schema_version",
                "request_digest",
            },
            label="verification request",
        )
        commands_raw = raw["commands"]
        if not isinstance(commands_raw, list):
            raise ValueError("verification request commands must be a list")
        request = cls(
            run_id=_string(raw["run_id"], "verification request run ID"),
            workspace=_string(raw["workspace"], "verification request workspace"),
            workspace_state_before=_string(
                raw["workspace_state_before"],
                "verification request state",
            ),
            commands=tuple(CommandSpec.from_dict(item) for item in commands_raw),
            schema_version=_string(
                raw["schema_version"],
                "verification request schema",
            ),
        )
        recorded = _string(raw["request_digest"], "verification request digest")
        if recorded != request.request_digest:
            raise ValueError("verification request digest does not match content")
        return request


@dataclass(frozen=True, slots=True)
class VerificationReceipt(WireModel):
    run_id: str
    workspace: str
    worktree_commit_sha: str
    started_at: str
    finished_at: str
    passed: bool
    commands: tuple[CommandResult, ...]
    workspace_state_before: str
    workspace_state_after: str
    workspace_unchanged: bool
    request_digest: str = ""
    schema_version: str = "sisyphus_harness.verification.v2"
    workspace_bundle_id: str | None = None
    profile_digest: str | None = None
    execution_identity_digest: str | None = None
    verifier_asset_bundle_id: str | None = None

    def __post_init__(self) -> None:
        _validate_run_id(self.run_id)
        for value, label in (
            (self.workspace, "verification receipt workspace"),
            (self.worktree_commit_sha, "verification receipt commit"),
            (self.started_at, "verification receipt start time"),
            (self.finished_at, "verification receipt finish time"),
            (self.workspace_state_before, "verification receipt state before"),
            (self.workspace_state_after, "verification receipt state after"),
        ):
            _string(value, label)
        if type(self.passed) is not bool or type(self.workspace_unchanged) is not bool:
            raise ValueError("verification receipt status flags must be boolean")
        if self.workspace_unchanged != (
            self.workspace_state_before == self.workspace_state_after
        ):
            raise ValueError("verification receipt workspace state claim is inconsistent")
        if self.schema_version not in {
            "sisyphus_harness.verification.v1",
            "sisyphus_harness.verification.v2",
            "sisyphus_harness.verification.v3",
        }:
            raise ValueError("unsupported verification receipt schema")
        if not self.request_digest:
            object.__setattr__(self, "request_digest", _legacy_request_digest(self))
        if _SHA256.fullmatch(self.request_digest) is None:
            raise ValueError("verification request digest must be SHA-256")
        binding_values = (
            self.workspace_bundle_id,
            self.profile_digest,
            self.execution_identity_digest,
            self.verifier_asset_bundle_id,
        )
        if self.schema_version in {
            "sisyphus_harness.verification.v1",
            "sisyphus_harness.verification.v2",
        }:
            if any(value is not None for value in binding_values):
                raise ValueError("legacy verification receipt cannot contain v3 bindings")
        else:
            _string(self.workspace_bundle_id, "verification receipt bundle ID")
            for value, label in (
                (self.profile_digest, "verification receipt profile digest"),
                (
                    self.execution_identity_digest,
                    "verification receipt execution identity digest",
                ),
            ):
                if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                    raise ValueError(f"{label} must be SHA-256")
            if self.verifier_asset_bundle_id is not None:
                _string(
                    self.verifier_asset_bundle_id,
                    "verification receipt verifier asset bundle ID",
                )
        if type(self.commands) is not tuple or any(
            not isinstance(command, CommandResult) for command in self.commands
        ):
            raise ValueError(
                "verification receipt commands must be an immutable command tuple"
            )
        if self.passed != (
            self.workspace_unchanged and all(command.passed for command in self.commands)
        ):
            raise ValueError("verification receipt pass status is inconsistent")

    @property
    def receipt_digest(self) -> str:
        return sha256_digest(self._unsigned_payload())

    def _unsigned_payload(self) -> dict[str, object]:
        payload = WireModel.to_dict(self)
        if self.schema_version in {
            "sisyphus_harness.verification.v1",
            "sisyphus_harness.verification.v2",
        }:
            payload.pop("workspace_bundle_id")
            payload.pop("profile_digest")
            payload.pop("execution_identity_digest")
            payload.pop("verifier_asset_bundle_id")
        if self.schema_version == "sisyphus_harness.verification.v1":
            payload.pop("request_digest")
        payload["criteria"] = _criteria_payload(self.commands)
        return payload

    def to_dict(self) -> dict[str, object]:
        payload = self._unsigned_payload()
        if self.schema_version in {
            "sisyphus_harness.verification.v2",
            "sisyphus_harness.verification.v3",
        }:
            payload["receipt_digest"] = self.receipt_digest
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> VerificationReceipt:
        if not isinstance(raw, dict):
            raise ValueError("verification receipt must be an object")
        schema = raw.get("schema_version")
        common = {
            "run_id",
            "workspace",
            "worktree_commit_sha",
            "started_at",
            "finished_at",
            "passed",
            "commands",
            "workspace_state_before",
            "workspace_state_after",
            "workspace_unchanged",
            "schema_version",
            "criteria",
        }
        if schema == "sisyphus_harness.verification.v1":
            required = common
        elif schema == "sisyphus_harness.verification.v2":
            required = common | {"request_digest", "receipt_digest"}
        elif schema == "sisyphus_harness.verification.v3":
            required = common | {
                "request_digest",
                "receipt_digest",
                "workspace_bundle_id",
                "profile_digest",
                "execution_identity_digest",
                "verifier_asset_bundle_id",
            }
        else:
            raise ValueError("unsupported verification receipt schema")
        raw = strict_object(raw, required=required, label="verification receipt")
        commands_raw = raw["commands"]
        if not isinstance(commands_raw, list):
            raise ValueError("verification receipt commands must be a list")
        commands = tuple(CommandResult.from_dict(item) for item in commands_raw)
        request_digest = raw.get("request_digest", "")
        receipt = cls(
            run_id=_string(raw["run_id"], "verification receipt run ID"),
            workspace=_string(raw["workspace"], "verification receipt workspace"),
            worktree_commit_sha=_string(
                raw["worktree_commit_sha"],
                "verification receipt commit",
            ),
            started_at=_string(raw["started_at"], "verification receipt start time"),
            finished_at=_string(raw["finished_at"], "verification receipt finish time"),
            passed=_bool(raw["passed"], "verification receipt passed"),
            commands=commands,
            workspace_state_before=_string(
                raw["workspace_state_before"],
                "verification receipt state before",
            ),
            workspace_state_after=_string(
                raw["workspace_state_after"],
                "verification receipt state after",
            ),
            workspace_unchanged=_bool(
                raw["workspace_unchanged"],
                "verification receipt workspace_unchanged",
            ),
            request_digest=(
                _string(request_digest, "verification receipt request digest")
                if request_digest
                else ""
            ),
            schema_version=_string(raw["schema_version"], "verification receipt schema"),
            workspace_bundle_id=_optional_string(
                raw.get("workspace_bundle_id"),
                "verification receipt bundle ID",
            ),
            profile_digest=_optional_string(
                raw.get("profile_digest"),
                "verification receipt profile digest",
            ),
            execution_identity_digest=_optional_string(
                raw.get("execution_identity_digest"),
                "verification receipt execution identity digest",
            ),
            verifier_asset_bundle_id=_optional_string(
                raw.get("verifier_asset_bundle_id"),
                "verification receipt verifier asset bundle ID",
            ),
        )
        if raw["criteria"] != _criteria_payload(commands):
            raise ValueError("verification receipt criteria projection is inconsistent")
        if receipt.schema_version in {
            "sisyphus_harness.verification.v2",
            "sisyphus_harness.verification.v3",
        }:
            recorded = _string(raw["receipt_digest"], "verification receipt digest")
            if recorded != receipt.receipt_digest:
                raise ValueError("verification receipt digest does not match content")
        return receipt


def _criteria_payload(commands: tuple[CommandResult, ...]) -> list[dict[str, object]]:
    return [
        {
            "criterion": criterion,
            "command_name": command.name,
            "passed": command.passed,
        }
        for command in commands
        for criterion in command.criteria
    ]


def _legacy_request_digest(receipt: VerificationReceipt) -> str:
    return sha256_digest(
        {
            "run_id": receipt.run_id,
            "workspace": receipt.workspace,
            "workspace_state_before": receipt.workspace_state_before,
            "commands": [
                {
                    "name": command.name,
                    "argv": list(command.argv),
                    "criteria": list(command.criteria),
                }
                for command in receipt.commands
            ],
        }
    )


def _validate_run_id(value: str) -> None:
    if _SAFE_RUN_ID.fullmatch(value) is None or value in {".", ".."}:
        raise ValueError("verification run ID contains unsafe characters")


def _string(raw: object, label: str) -> str:
    if not isinstance(raw, str) or not raw or "\0" in raw:
        raise ValueError(f"{label} must be a non-empty string")
    return raw


def _optional_string(raw: object, label: str) -> str | None:
    if raw is None:
        return None
    return _string(raw, label)


def _bool(raw: object, label: str) -> bool:
    if not isinstance(raw, bool):
        raise ValueError(f"{label} must be a boolean")
    return raw


def _string_tuple(raw: object, label: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ValueError(f"{label} must be a string list")
    return tuple(raw)


def _relative_path(raw: object, label: str) -> str:
    value = _string(raw, label)
    candidate = PurePosixPath(value)
    if (
        "\\" in value
        or candidate.is_absolute()
        or candidate.as_posix() != value
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ValueError(f"{label} must be a safe relative path")
    return value
