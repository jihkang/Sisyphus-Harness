from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import threading
from typing import Any, Callable

from .agent import AgentTask, LocalCodingAgent
from .authority import (
    agent_artifact_root,
    authority_database_path,
    policy_root,
    verification_artifact_root,
)
from .config import ProviderSettings, load_harness_config
from .evolution import CandidatePolicy
from .models import JobRecord
from .policy import PolicyRegistry
from .provider import ChatProvider, OpenAICompatibleProvider
from .queue import JobQueue, LeaseError
from .verifier import BoundedVerifier
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

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CodingJobPayload:
        allowed = {"task", "criteria", "config", "policy", "run_id"}
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
        if run_id is not None and (not isinstance(run_id, str) or not run_id):
            raise WorkerError("coding job run_id must be a non-empty string or null")
        return cls(
            task=task.strip(),
            criteria=tuple(normalized_criteria),
            config=config,
            policy=policy,
            run_id=run_id,
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
                config_path = contained_path(self.repo_root, payload.config)
                config = load_harness_config(config_path)
                policy = self._policy(config, payload.policy)
                result = LocalCodingAgent(
                    provider=self.provider_factory(config.provider),
                    verifier=BoundedVerifier(
                        verification_artifact_root(self.repo_root)
                    ),
                    agent_artifact_root=agent_artifact_root(self.repo_root),
                    limits=config.limits,
                    cadence=policy.cadence,
                    strategy_prompt=policy.strategy_prompt,
                    protected_write_paths=(config_path,),
                ).run(
                    self.repo_root,
                    AgentTask(payload.task, payload.criteria),
                    config.verification.selected_commands,
                    run_id=payload.run_id,
                )
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
