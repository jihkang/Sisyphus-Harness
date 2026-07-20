from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
from typing import Any, Callable

from .adapters.in_process import InProcessAgentRunFactory
from .authority import (
    agent_artifact_root,
    attempt_workspace_root,
    authority_database_path,
    policy_root,
    verification_artifact_root,
    workspace_bundle_root,
)
from .config import ProviderSettings, load_harness_config
from .contracts.agent import AgentTask
from .contracts.control import CodingJobResult
from .contracts.policy import CandidatePolicy
from .contracts.workspace import WorkspaceBundleRef
from .infra.workspace_bundle import FilesystemWorkspaceBundleStore
from .models import JobRecord
from .policy import PolicyRegistry
from .provider import ChatProvider, OpenAICompatibleProvider
from .queue import JobQueue, LeaseError
from .workspace import contained_path


class WorkerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CodingJobPayload:
    task: str
    criteria: tuple[str, ...]
    config: str
    policy: str
    run_id: str | None
    workspace_bundle: WorkspaceBundleRef | None = None
    config_sha256: str | None = None
    policy_snapshot: CandidatePolicy | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CodingJobPayload:
        allowed = {
            "task",
            "criteria",
            "config",
            "policy",
            "run_id",
            "workspace_bundle",
            "config_sha256",
            "policy_snapshot",
        }
        unknown = sorted(set(raw).difference(allowed))
        if unknown:
            raise WorkerError(
                f"coding job contains unknown fields: {', '.join(unknown)}"
            )
        task = raw.get("task")
        criteria = raw.get("criteria")
        config = raw.get("config", "sisyphus-harness.toml")
        policy = raw.get("policy", "config")
        run_id = raw.get("run_id")
        workspace_bundle_raw = raw.get("workspace_bundle")
        config_sha256 = raw.get("config_sha256")
        policy_snapshot_raw = raw.get("policy_snapshot")
        if not isinstance(task, str) or not task.strip():
            raise WorkerError("coding job task must be a non-empty string")
        if not isinstance(criteria, list) or not criteria:
            raise WorkerError("coding job criteria must be a non-empty list")
        normalized_criteria: list[str] = []
        for index, criterion in enumerate(criteria):
            if not isinstance(criterion, str) or not criterion.strip():
                raise WorkerError(
                    f"coding job criteria[{index}] must be a non-empty string"
                )
            normalized_criteria.append(criterion.strip())
        if not isinstance(config, str) or not config:
            raise WorkerError("coding job config must be a path string")
        if policy not in {"config", "active"}:
            raise WorkerError("coding job policy must be 'config' or 'active'")
        if run_id is not None and (
            not isinstance(run_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,82}", run_id) is None
        ):
            raise WorkerError(
                "coding job run_id must be a safe prefix of at most 83 characters"
            )
        snapshots = (
            workspace_bundle_raw,
            config_sha256,
            policy_snapshot_raw,
        )
        if any(value is not None for value in snapshots) and not all(
            value is not None for value in snapshots
        ):
            raise WorkerError(
                "coding job snapshot fields must be provided together"
            )
        workspace_bundle: WorkspaceBundleRef | None = None
        policy_snapshot: CandidatePolicy | None = None
        if workspace_bundle_raw is not None:
            try:
                workspace_bundle = WorkspaceBundleRef.from_dict(workspace_bundle_raw)
                policy_snapshot = CandidatePolicy.from_dict(policy_snapshot_raw)
            except ValueError as exc:
                raise WorkerError(f"coding job snapshot is invalid: {exc}") from exc
            if (
                not isinstance(config_sha256, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", config_sha256) is None
            ):
                raise WorkerError("coding job config digest must be SHA-256")
        return cls(
            task=task.strip(),
            criteria=tuple(normalized_criteria),
            config=config,
            policy=policy,
            run_id=run_id,
            workspace_bundle=workspace_bundle,
            config_sha256=config_sha256,
            policy_snapshot=policy_snapshot,
        )


class LeaseKeeper:
    def __init__(
        self,
        queue: JobQueue,
        job_id: str,
        *,
        worker_id: str,
        lease_seconds: float,
    ) -> None:
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("lease duration must be positive and finite")
        self.queue = queue
        self.job_id = job_id
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"sisyphus-lease-{job_id}",
            daemon=True,
        )
        self.lost_error: Exception | None = None

    def __enter__(self) -> LeaseKeeper:
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=max(1.0, self.lease_seconds))
        if self._thread.is_alive():
            raise WorkerError("lease heartbeat thread did not stop")

    def _run(self) -> None:
        interval = min(
            max(0.05, self.lease_seconds / 3),
            self.lease_seconds / 2,
        )
        while not self._stop.wait(interval):
            try:
                self.queue.heartbeat(
                    self.job_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
            except Exception as exc:
                self.lost_error = exc
                self._stop.set()
                return


class CodingWorker:
    def __init__(
        self,
        repo_root: Path,
        *,
        provider_factory: Callable[[ProviderSettings], ChatProvider] = (
            OpenAICompatibleProvider
        ),
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.queue = JobQueue(authority_database_path(self.repo_root))
        self.provider_factory = provider_factory

    def run_once(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
    ) -> JobRecord | None:
        job = self.queue.claim(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
        if job is None:
            return None
        with LeaseKeeper(
            self.queue,
            job.job_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        ) as keeper:
            try:
                if job.kind != "coding-agent":
                    raise WorkerError(f"unsupported job kind: {job.kind}")
                payload = CodingJobPayload.from_dict(job.payload)
                result = self._execute_attempt(job, payload, keeper)
                if keeper.lost_error is not None:
                    raise LeaseError(f"worker lost lease: {keeper.lost_error}")
                if result.success:
                    return self.queue.complete(
                        job.job_id,
                        worker_id=worker_id,
                        result=result.to_dict(),
                    )
                return self.queue.fail(
                    job.job_id,
                    worker_id=worker_id,
                    result=result.to_dict(),
                )
            except Exception as exc:
                if keeper.lost_error is not None:
                    raise LeaseError(f"worker lost lease: {keeper.lost_error}") from exc
                return self.queue.fail(
                    job.job_id,
                    worker_id=worker_id,
                    result={
                        "success": False,
                        "reason": f"{type(exc).__name__}: {exc}",
                    },
                )

    def _execute_attempt(
        self,
        job: JobRecord,
        payload: CodingJobPayload,
        keeper: LeaseKeeper,
    ) -> CodingJobResult:
        bundle_store = FilesystemWorkspaceBundleStore(
            workspace_bundle_root(self.repo_root)
        )
        source_bundle = self._source_bundle(bundle_store, payload)
        attempt_id = f"{job.job_id}/attempt-{job.attempts:04d}"
        attempt_root = attempt_workspace_root(self.repo_root) / job.job_id / (
            f"attempt-{job.attempts:04d}"
        )
        if attempt_root.exists() or attempt_root.is_symlink():
            raise WorkerError(f"attempt workspace already exists: {attempt_id}")
        attempt_root.parent.mkdir(parents=True, exist_ok=True)
        workspace = attempt_root / "workspace"
        try:
            bundle_store.materialize(source_bundle, workspace)
            _initialize_attempt_repository(workspace)
            config_path = contained_path(
                workspace,
                payload.config,
                require_relative=True,
            )
            if payload.config_sha256 is not None:
                actual_config_digest = _sha256_file(config_path)
                if actual_config_digest != payload.config_sha256:
                    raise WorkerError(
                        "materialized config does not match the submitted snapshot"
                    )
            config = load_harness_config(config_path)
            policy = payload.policy_snapshot or self._policy(config, payload.policy)
            run_prefix = payload.run_id or job.job_id
            artifact_run_id = f"{run_prefix}-attempt-{job.attempts:04d}"
            agent_result = InProcessAgentRunFactory(
                provider=self.provider_factory(config.provider),
                limits=config.limits,
                protected_write_paths=(config_path,),
            ).create(
                policy=policy,
                agent_artifact_root=agent_artifact_root(self.repo_root),
                verification_artifact_root=verification_artifact_root(
                    self.repo_root
                ),
            ).run(
                workspace,
                AgentTask(payload.task, payload.criteria),
                config.verification.selected_commands,
                run_id=artifact_run_id,
            )
            if keeper.lost_error is not None:
                raise LeaseError(f"worker lost lease: {keeper.lost_error}")
            output_bundle = bundle_store.create(workspace)
            return CodingJobResult(
                job_id=job.job_id,
                attempt=job.attempts,
                attempt_id=attempt_id,
                success=agent_result.success,
                source_bundle=source_bundle,
                output_bundle=output_bundle,
                agent_result=agent_result,
            )
        finally:
            shutil.rmtree(attempt_root, ignore_errors=True)

    def _source_bundle(
        self,
        bundle_store: FilesystemWorkspaceBundleStore,
        payload: CodingJobPayload,
    ) -> WorkspaceBundleRef:
        if payload.workspace_bundle is None:
            return bundle_store.create(self.repo_root)
        stored = bundle_store.load(payload.workspace_bundle.bundle_id)
        if stored != payload.workspace_bundle:
            raise WorkerError(
                "submitted workspace bundle does not match stored authority"
            )
        return stored

    def _policy(self, config, source: str) -> CandidatePolicy:
        if source == "config":
            return CandidatePolicy(
                strategy_prompt=config.strategy_prompt,
                cadence=config.cadence,
            )
        active = PolicyRegistry(policy_root(self.repo_root)).load_active()
        if active is None:
            raise WorkerError("no active evolved policy is available")
        return active


def _initialize_attempt_repository(workspace: Path) -> None:
    environment = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    commands = (
        ("init", "-q"),
        ("config", "user.email", "sisyphus-harness@example.invalid"),
        ("config", "user.name", "Sisyphus Harness"),
        ("add", "--all"),
        ("commit", "-q", "--allow-empty", "-m", "materialized attempt baseline"),
    )
    for args in commands:
        completed = subprocess.run(
            ["git", *args],
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            raise WorkerError(
                completed.stderr.strip()
                or f"failed to initialize attempt repository: {' '.join(args)}"
            )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
