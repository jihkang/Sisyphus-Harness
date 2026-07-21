from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from sisyphus_harness.adapters.bundle_verification import BundleVerificationAdapter
from sisyphus_harness.adapters.docker_verifier import DockerVerifierTransport
from sisyphus_harness.adapters.in_process import InProcessVerificationAdapter
from sisyphus_harness.config import (
    AgentLimits,
    ConfigError,
    EvolutionSettings,
    ExecutionSettings,
    HarnessConfig,
    ProviderSettings,
    VerificationConfig,
)
from sisyphus_harness.contracts.policy import CadencePolicy, CandidatePolicy
from sisyphus_harness.contracts.verification import CommandSpec
from sisyphus_harness.provider import ChatResponse
from sisyphus_harness.runtime import build_agent_run, build_verification_adapter

from .helpers import create_git_repo


class _Provider:
    def complete(self, messages) -> ChatResponse:
        return ChatResponse('{"type":"finish","summary":"done"}')


def _config(execution: ExecutionSettings) -> HarnessConfig:
    command = CommandSpec(
        name="behavior",
        argv=("python3", "-c", "pass"),
        timeout_seconds=5,
        criteria=("behavior passes",),
    )
    return HarnessConfig(
        provider=ProviderSettings("http://127.0.0.1:8080/v1", "local"),
        limits=AgentLimits(),
        cadence=CadencePolicy(),
        strategy_prompt="bounded",
        verification=VerificationConfig(
            commands={"behavior": command},
            selected_names=("behavior",),
        ),
        evolution=EvolutionSettings(),
        execution=execution,
    )


class RuntimeCompositionTests(unittest.TestCase):
    def test_untrusted_mode_is_default_and_uses_docker_bundle_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = create_git_repo(Path(directory) / "repository")
            config = _config(ExecutionSettings(writable_paths=("src",)))

            verifier = build_verification_adapter(root, config)

            self.assertIsInstance(verifier, BundleVerificationAdapter)
            self.assertIsInstance(verifier.verifier, DockerVerifierTransport)

    def test_untrusted_agent_requires_positive_write_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = create_git_repo(Path(directory) / "repository")
            config_path = repository / "sisyphus-harness.toml"
            config_path.write_text("fixture", encoding="utf-8")
            config = _config(ExecutionSettings())

            with self.assertRaisesRegex(ConfigError, "writable_paths"):
                build_agent_run(
                    authority_root=repository,
                    workspace=repository,
                    config_path=config_path,
                    config=config,
                    provider=_Provider(),
                    policy=CandidatePolicy("bounded", CadencePolicy()),
                )

    def test_host_verifier_requires_explicit_trusted_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = create_git_repo(Path(directory) / "repository")
            trusted = replace(
                _config(ExecutionSettings()),
                execution=ExecutionSettings(trust_mode="trusted-in-process"),
            )

            verifier = build_verification_adapter(root, trusted)

            self.assertIsInstance(verifier, InProcessVerificationAdapter)


if __name__ == "__main__":
    unittest.main()
