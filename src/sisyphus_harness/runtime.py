from __future__ import annotations

from pathlib import Path

from .adapters.bundle_verification import BundleVerificationAdapter
from .adapters.docker_verifier import DockerVerifierTransport
from .adapters.in_process import (
    InProcessAgentRunFactory,
    InProcessVerificationAdapter,
)
from .authority import (
    agent_artifact_root,
    authority_database_path,
    verification_artifact_root,
    workspace_bundle_root,
)
from .config import ConfigError, HarnessConfig
from .contracts.policy import CandidatePolicy
from .infra.workspace_bundle import FilesystemWorkspaceBundleStore
from .infra.control_outcomes import SQLiteTaskOutcomeAuthority
from .ports.agent_run import AgentRunPort
from .ports.control_outcomes import TaskOutcomeServicePort
from .ports.verification import VerificationPort
from .provider import ChatProvider
from .services.control_outcomes import ControlTaskOutcomeService
from .services.evidence_contract import ControlEvidenceContractService
from .workspace import contained_path


def build_verification_adapter(
    authority_root: Path,
    config: HarnessConfig,
) -> VerificationPort:
    if config.execution.trust_mode == "trusted-in-process":
        return InProcessVerificationAdapter.from_artifact_root(
            verification_artifact_root(authority_root)
        )
    bundle_store = FilesystemWorkspaceBundleStore(
        workspace_bundle_root(authority_root)
    )
    transport = DockerVerifierTransport(
        bundle_store=bundle_store.root,
        artifact_root=verification_artifact_root(authority_root),
        image=config.execution.verifier_image,
    )
    return BundleVerificationAdapter(bundle_store=bundle_store, verifier=transport)


def build_agent_run(
    *,
    authority_root: Path,
    workspace: Path,
    config_path: Path,
    config: HarnessConfig,
    provider: ChatProvider,
    policy: CandidatePolicy,
) -> AgentRunPort:
    allowed_write_paths: tuple[Path, ...] | None = None
    if config.execution.trust_mode == "untrusted-contained":
        if not config.execution.writable_paths:
            raise ConfigError(
                "untrusted-contained execution requires at least one "
                "execution.writable_paths entry"
            )
        allowed_write_paths = tuple(
            contained_path(workspace, path, require_relative=True)
            for path in config.execution.writable_paths
        )
    verifier = build_verification_adapter(authority_root, config)
    return InProcessAgentRunFactory(
        provider=provider,
        limits=config.limits,
        protected_write_paths=(config_path,),
        allowed_write_paths=allowed_write_paths,
        verifier=verifier,
    ).create(
        policy=policy,
        agent_artifact_root=agent_artifact_root(authority_root),
        verification_artifact_root=verification_artifact_root(authority_root),
    )


def build_control_task_outcome_service(
    authority_root: Path,
    config: HarnessConfig,
) -> TaskOutcomeServicePort:
    """Compose Control final adjudication with an always-contained verifier."""

    bundle_store = FilesystemWorkspaceBundleStore(
        workspace_bundle_root(authority_root)
    )
    verifier = DockerVerifierTransport(
        bundle_store=bundle_store.root,
        artifact_root=verification_artifact_root(authority_root),
        image=config.execution.verifier_image,
    )
    return ControlTaskOutcomeService(
        adjudicator=ControlEvidenceContractService(verifier),
        authority=SQLiteTaskOutcomeAuthority(
            authority_database_path(authority_root)
        ),
    )


__all__ = [
    "build_agent_run",
    "build_control_task_outcome_service",
    "build_verification_adapter",
]
