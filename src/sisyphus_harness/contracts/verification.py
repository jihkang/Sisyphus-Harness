from __future__ import annotations

from dataclasses import dataclass
import math

from .codec import WireModel


@dataclass(frozen=True, slots=True)
class CommandSpec(WireModel):
    name: str
    argv: tuple[str, ...]
    timeout_seconds: float
    criteria: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("verification command name must be non-empty")
        if not self.argv or any(
            not item.strip() or "\0" in item for item in self.argv
        ):
            raise ValueError(f"verification command {self.name!r} requires non-empty argv")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError(f"verification command {self.name!r} requires a positive timeout")
        if (
            not self.criteria
            or any(not item.strip() for item in self.criteria)
            or len(set(self.criteria)) != len(self.criteria)
        ):
            raise ValueError(
                f"verification command {self.name!r} requires unique acceptance criteria"
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
    schema_version: str = "sisyphus_harness.verification.v1"

    def to_dict(self) -> dict[str, object]:
        criteria = [
            {
                "criterion": criterion,
                "command_name": command.name,
                "passed": command.passed,
            }
            for command in self.commands
            for criterion in command.criteria
        ]
        payload = super().to_dict()
        payload["criteria"] = criteria
        return payload
