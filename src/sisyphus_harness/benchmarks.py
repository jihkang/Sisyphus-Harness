from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid
from typing import Any

from .adapters.in_process import InProcessAgentRunFactory
from .config import AgentLimits
from .contracts.agent import AgentTask
from .contracts.evolution import EvaluationObservation
from .contracts.policy import CandidatePolicy
from .contracts.verification import CommandSpec, VerificationReceipt
from .infra.verification_evidence import FilesystemVerificationEvidenceStore
from .ports.agent_run import AgentRunFactoryPort
from .provider import ChatProvider
from .workspace import contained_path


class BenchmarkError(RuntimeError):
    pass


_SAFE_CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}")
_IGNORED_FIXTURE_NAMES = {".git", ".sisyphus-harness", "__pycache__"}


@dataclass(frozen=True, slots=True)
class BenchmarkVerifier:
    name: str
    criterion: str
    script: Path


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    instruction: str
    acceptance_criteria: tuple[str, ...]
    workspace_source: Path
    verifiers: tuple[BenchmarkVerifier, ...]
    timeout_seconds: float

    def to_example(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "instruction": self.instruction,
            "acceptance_criteria": list(self.acceptance_criteria),
            "workspace_source": str(self.workspace_source),
            "verifiers": [
                {
                    "name": verifier.name,
                    "criterion": verifier.criterion,
                    "script": str(verifier.script),
                }
                for verifier in self.verifiers
            ],
            "timeout_seconds": self.timeout_seconds,
        }


def load_benchmark_dataset(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"invalid benchmark dataset: {path}") from exc
    if not isinstance(raw, dict):
        raise BenchmarkError("benchmark dataset must be an object")
    _reject_unknown(raw, {"schema_version", "cases"}, "benchmark dataset")
    if raw.get("schema_version") != "sisyphus_harness.benchmark_dataset.v1":
        raise BenchmarkError("unsupported benchmark dataset schema")
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise BenchmarkError("benchmark dataset requires non-empty cases")
    cases: list[BenchmarkCase] = []
    seen: set[str] = set()
    for index, relative in enumerate(raw_cases):
        if not isinstance(relative, str) or not relative:
            raise BenchmarkError(f"benchmark cases[{index}] must be a path string")
        case_dir = contained_path(path.parent, relative, require_relative=True)
        case = _load_case(case_dir)
        if case.case_id in seen:
            raise BenchmarkError(f"duplicate benchmark case ID: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    return [case.to_example() for case in cases]


class CodingAgentBenchmarkEvaluator:
    def __init__(
        self,
        *,
        limits: AgentLimits,
        rollout_root: Path,
        provider: ChatProvider | None = None,
        agent_factory: AgentRunFactoryPort | None = None,
    ) -> None:
        if agent_factory is None:
            if provider is None:
                raise ValueError(
                    "benchmark evaluator requires a provider or agent factory"
                )
            agent_factory = InProcessAgentRunFactory(
                provider=provider,
                limits=limits,
            )
        self.provider = provider
        self.limits = limits
        self.rollout_root = rollout_root
        self.agent_factory = agent_factory
        self.rollout_root.mkdir(parents=True, exist_ok=True)

    def __call__(
        self,
        policy: CandidatePolicy,
        example: dict[str, Any],
    ) -> EvaluationObservation:
        try:
            return self._evaluate(policy, example)
        except Exception as exc:
            return EvaluationObservation(
                score=0.0,
                success=False,
                hard_gate_passed=False,
                diagnostics={
                    "case_id": str(example.get("id", "unknown")),
                    "error": f"{type(exc).__name__}: {exc}",
                },
                scores={
                    "correctness": 0.0,
                    "step_efficiency": 0.0,
                    "compaction_efficiency": 0.0,
                },
            )

    def _evaluate(
        self,
        policy: CandidatePolicy,
        example: dict[str, Any],
    ) -> EvaluationObservation:
        case_id = validate_benchmark_case_id(_required_string(example, "id"))
        instruction = _required_string(example, "instruction")
        criteria_raw = example.get("acceptance_criteria")
        if not isinstance(criteria_raw, list) or not criteria_raw:
            raise BenchmarkError("benchmark acceptance_criteria must be non-empty")
        criteria = tuple(
            _nonempty_item(item, "acceptance_criteria") for item in criteria_raw
        )
        workspace_source = Path(_required_string(example, "workspace_source"))
        verifiers_raw = example.get("verifiers")
        if not isinstance(verifiers_raw, list) or not verifiers_raw:
            raise BenchmarkError("benchmark verifiers must be non-empty")
        verifiers: list[tuple[str, str, Path]] = []
        for raw_verifier in verifiers_raw:
            if not isinstance(raw_verifier, dict):
                raise BenchmarkError("benchmark verifier must be an object")
            _reject_unknown(
                raw_verifier,
                {"name", "criterion", "script"},
                "benchmark verifier",
            )
            verifiers.append(
                (
                    _required_string(raw_verifier, "name"),
                    _required_string(raw_verifier, "criterion"),
                    Path(_required_string(raw_verifier, "script")),
                )
            )
        names = [name for name, _, _ in verifiers]
        if len(set(names)) != len(names):
            raise BenchmarkError("benchmark verifier names must be unique")
        if tuple(criterion for _, criterion, _ in verifiers) != criteria:
            raise BenchmarkError(
                "benchmark verifiers must map one-to-one to acceptance criteria in order"
            )
        timeout = example.get("timeout_seconds")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise BenchmarkError("benchmark timeout_seconds must be numeric")
        rollout_id = (
            f"{case_id}-{policy.candidate_hash.removeprefix('sha256:')[:12]}-"
            f"{uuid.uuid4().hex[:10]}"
        )
        rollout_dir = self.rollout_root / rollout_id
        workspace = rollout_dir / "workspace"
        verification_root = rollout_dir / "verification"
        _copy_fixture(workspace_source, workspace)
        _initialize_git_repository(workspace)
        agent = self.agent_factory.create(
            policy=policy,
            agent_artifact_root=rollout_dir / "agent",
            verification_artifact_root=verification_root,
        )
        verification_commands = tuple(
            CommandSpec(
                name=f"benchmark-{case_id}-{name}",
                argv=(sys.executable, str(script)),
                timeout_seconds=float(timeout),
                criteria=(criterion,),
            )
            for name, criterion, script in verifiers
        )
        result = agent.run(
            workspace,
            AgentTask(instruction, criteria),
            verification_commands,
            run_id="agent",
        )
        if not result.verification_artifacts:
            raise BenchmarkError("agent result did not include verification evidence")
        evidence_reference = result.verification_artifacts[-1]
        receipt = FilesystemVerificationEvidenceStore(
            verification_root
        ).read_receipt(evidence_reference)
        _validate_scoring_receipt(
            receipt,
            workspace=workspace,
            result_workspace_state=result.workspace_state_after,
            expected_criteria=criteria,
            expected_commands=verification_commands,
        )
        criterion_pass_rate = _criterion_pass_rate(receipt, criteria)
        verified_success = result.success and receipt.passed
        step_efficiency = max(
            0.0,
            1.0 - max(0, result.steps - 1) / max(1, self.limits.max_steps - 1),
        )
        compaction_efficiency = max(
            0.0,
            1.0 - result.compactions / max(1, self.limits.max_compactions),
        )
        score = (
            0.70 * criterion_pass_rate
            + 0.15 * float(verified_success)
            + 0.10 * step_efficiency
            + 0.05 * compaction_efficiency
        )
        unsafe_reason = not receipt.workspace_unchanged or result.reason in {
            "verification command mutated the workspace",
            "tool failed after mutating workspace",
        }
        return EvaluationObservation(
            score=score,
            success=verified_success,
            hard_gate_passed=not unsafe_reason,
            diagnostics={
                "case_id": case_id,
                "instruction": instruction,
                "result": result.to_dict(),
                "criterion_pass_rate": criterion_pass_rate,
                "verification_evidence": evidence_reference.to_dict(),
                "verification_receipt_digest": receipt.receipt_digest,
                "trace_summary": _trace_summary(Path(result.artifact_path)),
                "rollout_path": str(rollout_dir),
            },
            scores={
                "correctness": criterion_pass_rate,
                "step_efficiency": step_efficiency,
                "compaction_efficiency": compaction_efficiency,
            },
        )


def _criterion_pass_rate(
    receipt: VerificationReceipt,
    expected_criteria: tuple[str, ...],
) -> float:
    status = {
        criterion: command.passed
        for command in receipt.commands
        for criterion in command.criteria
    }
    return sum(status.get(criterion) is True for criterion in expected_criteria) / len(
        expected_criteria
    )


def _validate_scoring_receipt(
    receipt: VerificationReceipt,
    *,
    workspace: Path,
    result_workspace_state: str,
    expected_criteria: tuple[str, ...],
    expected_commands: tuple[CommandSpec, ...],
) -> None:
    receipt_command_profile = tuple(
        (command.name, command.argv, command.criteria)
        for command in receipt.commands
    )
    expected_command_profile = tuple(
        (command.name, command.argv, command.criteria)
        for command in expected_commands
    )
    if receipt_command_profile != expected_command_profile:
        raise BenchmarkError(
            "verification receipt commands do not match the benchmark request"
        )
    receipt_criteria = tuple(
        criterion
        for command in receipt.commands
        for criterion in command.criteria
    )
    if receipt_criteria != expected_criteria:
        raise BenchmarkError(
            "verification receipt criteria do not match the benchmark case"
        )
    if Path(receipt.workspace) != workspace.resolve():
        raise BenchmarkError("verification receipt workspace does not match rollout")
    if receipt.workspace_state_after != result_workspace_state:
        raise BenchmarkError("verification receipt does not attest the final workspace")


def _trace_summary(agent_artifact: Path, *, limit: int = 64) -> dict[str, object]:
    step_paths = sorted((agent_artifact / "steps").glob("*.json"))
    actions: list[dict[str, object]] = []
    for step_path in step_paths[:limit]:
        try:
            raw = json.loads(step_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            actions.append(
                {
                    "step": step_path.stem,
                    "event_kind": "invalid_receipt",
                }
            )
            continue
        decision = raw.get("decision")
        event = raw.get("event")
        action: dict[str, object] = {"step": raw.get("step")}
        if isinstance(decision, dict):
            action["decision_type"] = decision.get("type")
            if isinstance(decision.get("tool"), str):
                action["tool"] = decision["tool"]
        if isinstance(event, dict):
            action["event_kind"] = event.get("kind")
            action["mutated"] = event.get("mutated", False)
            error = event.get("error")
            if isinstance(error, str):
                action["error"] = error[:500]
            output = event.get("output")
            if isinstance(output, dict):
                path = output.get("path")
                if isinstance(path, str):
                    action["path"] = path
                matches = output.get("matches")
                if isinstance(matches, list):
                    action["match_count"] = len(matches)
            if event.get("kind") == "verification":
                action["verification_passed"] = event.get("passed")
                criteria = event.get("criteria")
                if isinstance(criteria, list):
                    action["failed_criteria"] = [
                        item.get("criterion")
                        for item in criteria
                        if isinstance(item, dict)
                        and item.get("passed") is False
                        and isinstance(item.get("criterion"), str)
                    ]
        actions.append(action)
    return {
        "actions": actions,
        "total_steps": len(step_paths),
        "truncated": len(step_paths) > limit,
    }


def _load_case(case_dir: Path) -> BenchmarkCase:
    descriptor = case_dir / "case.json"
    try:
        raw = json.loads(descriptor.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"invalid benchmark case: {case_dir}") from exc
    if not isinstance(raw, dict):
        raise BenchmarkError(f"benchmark case must be an object: {case_dir}")
    _reject_unknown(
        raw,
        {
            "schema_version",
            "id",
            "instruction",
            "acceptance_criteria",
            "workspace",
            "verifiers",
            "timeout_seconds",
        },
        f"benchmark case {case_dir.name}",
    )
    if raw.get("schema_version") != "sisyphus_harness.benchmark_case.v1":
        raise BenchmarkError(f"unsupported benchmark case schema: {case_dir}")
    criteria_raw = raw.get("acceptance_criteria")
    if not isinstance(criteria_raw, list) or not criteria_raw:
        raise BenchmarkError(f"benchmark case requires acceptance criteria: {case_dir}")
    workspace = contained_path(
        case_dir,
        _required_string(raw, "workspace"),
        require_relative=True,
    )
    if not workspace.is_dir():
        raise BenchmarkError(f"benchmark workspace does not exist: {workspace}")
    raw_verifiers = raw.get("verifiers")
    if not isinstance(raw_verifiers, list) or not raw_verifiers:
        raise BenchmarkError(f"benchmark case requires verifiers: {case_dir}")
    verifiers: list[BenchmarkVerifier] = []
    verifier_names: set[str] = set()
    verifier_criteria: list[str] = []
    for raw_verifier in raw_verifiers:
        if not isinstance(raw_verifier, dict):
            raise BenchmarkError("benchmark verifier must be an object")
        _reject_unknown(
            raw_verifier,
            {"name", "criterion", "script"},
            "benchmark verifier",
        )
        name = _required_string(raw_verifier, "name")
        criterion = _required_string(raw_verifier, "criterion")
        script = contained_path(
            case_dir,
            _required_string(raw_verifier, "script"),
            require_relative=True,
        )
        if name in verifier_names:
            raise BenchmarkError(f"duplicate benchmark verifier name: {name}")
        if not script.is_file():
            raise BenchmarkError(f"benchmark verifier does not exist: {script}")
        verifier_names.add(name)
        verifier_criteria.append(criterion)
        verifiers.append(BenchmarkVerifier(name, criterion, script))
    criteria = tuple(
        _nonempty_item(item, "acceptance_criteria") for item in criteria_raw
    )
    if tuple(verifier_criteria) != criteria:
        raise BenchmarkError(
            "benchmark verifiers must map one-to-one to acceptance criteria in order"
        )
    timeout = raw.get("timeout_seconds", 20)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or timeout <= 0
    ):
        raise BenchmarkError("benchmark timeout_seconds must be positive")
    return BenchmarkCase(
        case_id=validate_benchmark_case_id(_required_string(raw, "id")),
        instruction=_required_string(raw, "instruction"),
        acceptance_criteria=criteria,
        workspace_source=workspace,
        verifiers=tuple(verifiers),
        timeout_seconds=float(timeout),
    )


def _initialize_git_repository(workspace: Path) -> None:
    environment = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
    }
    commands = (
        ("init", "-q"),
        ("config", "user.name", "Sisyphus Harness Benchmark"),
        ("config", "user.email", "benchmark@example.invalid"),
        ("add", "--all"),
        ("commit", "-q", "-m", "benchmark fixture"),
    )
    for args in commands:
        completed = subprocess.run(
            ["git", *args],
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if completed.returncode != 0:
            raise BenchmarkError(
                completed.stderr.strip()
                or completed.stdout.strip()
                or f"git command failed: {' '.join(args)}"
            )


def validate_benchmark_case_id(case_id: str) -> str:
    normalized = case_id.strip()
    if _SAFE_CASE_ID.fullmatch(normalized) is None or normalized in {".", ".."}:
        raise BenchmarkError("benchmark case ID contains unsafe characters")
    return normalized


def _copy_fixture(source: Path, destination: Path) -> None:
    root = source.resolve()
    _validate_fixture_tree(root)
    shutil.copytree(
        root,
        destination,
        symlinks=True,
        ignore=shutil.ignore_patterns(
            ".git",
            ".sisyphus-harness",
            "__pycache__",
            "*.pyc",
            "*.pyo",
        ),
    )


def _validate_fixture_tree(root: Path) -> None:
    def visit(directory: Path) -> None:
        with os.scandir(directory) as children:
            entries = list(children)
        for child in entries:
            if child.name in _IGNORED_FIXTURE_NAMES or any(
                fnmatch.fnmatch(child.name, pattern)
                for pattern in ("*.pyc", "*.pyo")
            ):
                continue
            path = Path(child.path)
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raw_target = os.readlink(path)
                candidate = Path(raw_target)
                if candidate.is_absolute():
                    raise BenchmarkError(
                        f"benchmark fixture symlink target must be relative: {path.name}"
                    )
                try:
                    target = (path.parent / candidate).resolve(strict=False)
                    target.relative_to(root)
                except (RuntimeError, ValueError) as exc:
                    raise BenchmarkError(
                        f"benchmark fixture symlink escapes workspace: {path.name}"
                    ) from exc
            elif stat.S_ISDIR(metadata.st_mode):
                visit(path)
            elif not stat.S_ISREG(metadata.st_mode):
                raise BenchmarkError(
                    f"benchmark fixture contains unsupported entry: "
                    f"{path.relative_to(root).as_posix()}"
                )

    visit(root)


def _reject_unknown(
    raw: dict[str, object],
    allowed: set[str],
    field: str,
) -> None:
    unknown = sorted(set(raw).difference(allowed))
    if unknown:
        raise BenchmarkError(f"{field} contains unknown fields: {', '.join(unknown)}")


def _required_string(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkError(f"{field} must be a non-empty string")
    return value.strip()


def _nonempty_item(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkError(f"{field} entries must be non-empty strings")
    return value.strip()
