from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sisyphus_harness.adapters.in_process import (
    InProcessAgentRunAdapter,
    InProcessAgentRunFactory,
    InProcessVerificationAdapter,
)
from sisyphus_harness.config import AgentLimits
from sisyphus_harness.contracts import (
    AgentResult,
    AgentTask,
    CadencePolicy,
    CandidatePolicy,
    CommandSpec,
    VerificationReceipt,
)
from sisyphus_harness.ports import AgentRunFactoryPort, AgentRunPort, VerificationPort


class RecordingVerificationPort:
    def __init__(self, result: VerificationReceipt) -> None:
        self.result = result
        self.calls: list[tuple[Path, tuple[CommandSpec, ...], str | None]] = []

    def verify(self, workspace, commands, *, run_id=None):
        self.calls.append((workspace, commands, run_id))
        return self.result


class RecordingAgentRunPort:
    def __init__(self, result: AgentResult) -> None:
        self.result = result
        self.calls: list[
            tuple[Path, AgentTask, tuple[CommandSpec, ...], str | None]
        ] = []

    def run(self, workspace, task, commands, *, run_id=None):
        self.calls.append((workspace, task, commands, run_id))
        return self.result


class NeverProvider:
    def complete(self, messages):
        raise AssertionError("provider must not run while creating an adapter")


class InProcessAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.command = CommandSpec(
            name="check",
            argv=("python", "-c", "pass"),
            timeout_seconds=10,
            criteria=("behavior passes",),
        )
        self.task = AgentTask("Fix behavior.", ("behavior passes",))

    def test_verification_adapter_delegates_without_transforming_result(self) -> None:
        receipt = VerificationReceipt(
            run_id="verify-1",
            workspace="/workspace",
            worktree_commit_sha="abc",
            started_at="2026-07-18T00:00:00Z",
            finished_at="2026-07-18T00:00:01Z",
            passed=True,
            commands=(),
            workspace_state_before="before",
            workspace_state_after="before",
            workspace_unchanged=True,
        )
        delegate = RecordingVerificationPort(receipt)
        adapter = InProcessVerificationAdapter(delegate)

        result = adapter.verify(Path("workspace"), (self.command,), run_id="verify-1")

        self.assertIs(result, receipt)
        self.assertEqual(
            delegate.calls,
            [(Path("workspace"), (self.command,), "verify-1")],
        )
        self.assertIsInstance(adapter, VerificationPort)

    def test_agent_adapter_delegates_without_transforming_result(self) -> None:
        agent_result = AgentResult(
            run_id="agent-1",
            success=True,
            reason="verified",
            steps=2,
            compactions=0,
            verifications=1,
            workspace_state_before="before",
            workspace_state_after="after",
            changed_paths=("module.py",),
            artifact_path="artifacts/agent-1",
        )
        delegate = RecordingAgentRunPort(agent_result)
        adapter = InProcessAgentRunAdapter(delegate)

        result = adapter.run(
            Path("workspace"),
            self.task,
            (self.command,),
            run_id="agent-1",
        )

        self.assertIs(result, agent_result)
        self.assertEqual(
            delegate.calls,
            [(Path("workspace"), self.task, (self.command,), "agent-1")],
        )
        self.assertIsInstance(adapter, AgentRunPort)

    def test_factory_builds_ports_without_executing_the_model(self) -> None:
        factory = InProcessAgentRunFactory(
            provider=NeverProvider(),
            limits=AgentLimits(),
        )
        policy = CandidatePolicy(
            strategy_prompt="Inspect before editing.",
            cadence=CadencePolicy(),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = factory.create(
                policy=policy,
                agent_artifact_root=root / "agent",
                verification_artifact_root=root / "verification",
            )

        self.assertIsInstance(factory, AgentRunFactoryPort)
        self.assertIsInstance(adapter, AgentRunPort)


if __name__ == "__main__":
    unittest.main()
