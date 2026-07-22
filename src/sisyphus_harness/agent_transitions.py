from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_artifacts import AgentTurn
from .agent_context import update_known_file_state
from .agent_state import AgentRunState, AgentTermination
from .contracts.artifacts import ArtifactRef
from .contracts.verification import CommandSpec, VerificationReceipt
from .contracts.workspace import WorkspaceSnapshot
from .ports.verification import VerificationEvidencePort, VerificationPort
from .tools import ToolError, WorkspaceTools
from .workspace import snapshot_workspace


@dataclass(frozen=True, slots=True)
class AgentStepTransition:
    event: dict[str, Any]
    after: WorkspaceSnapshot
    termination: AgentTermination | None = None


@dataclass(frozen=True, slots=True)
class AgentToolTransitionHandler:
    tools: WorkspaceTools
    workspace: Path
    stagnation_limit: int

    def execute(
        self,
        turn: AgentTurn,
        state: AgentRunState,
    ) -> AgentStepTransition:
        decision = turn.decision
        if decision is None or decision.kind != "tool" or decision.tool is None:
            raise TypeError("tool transition requires a parsed tool decision")
        tool_failed = False
        try:
            outcome = self.tools.execute(decision.tool, decision.arguments)
            after = snapshot_workspace(self.workspace)
            state_changed = turn.before.state_hash != after.state_hash
            if state_changed != outcome.mutated:
                raise ToolError(
                    "tool mutation report does not match workspace state transition"
                )
            event = {
                "kind": "tool",
                "step": turn.step,
                "tool": decision.tool,
                "arguments": decision.arguments,
                "reason": decision.reason,
                "output": outcome.output,
                "mutated": outcome.mutated,
            }
            state.working_file = update_known_file_state(
                state.known_file_hashes,
                state.working_file,
                decision.tool,
                decision.arguments,
                outcome.output,
            )
            if outcome.mutated:
                state.mutations_since_verify += 1
        except ToolError as exc:
            tool_failed = True
            after = snapshot_workspace(self.workspace)
            event = {
                "kind": "tool_error",
                "step": turn.step,
                "tool": decision.tool,
                "arguments": decision.arguments,
                "error": str(exc),
                "mutated": turn.before.state_hash != after.state_hash,
            }

        state.events.append(event)
        if tool_failed and event["mutated"]:
            return AgentStepTransition(
                event=event,
                after=after,
                termination=AgentTermination(
                    success=False,
                    reason="tool failed after mutating workspace",
                    steps=turn.step,
                ),
            )

        if event.get("mutated") and state.observe_workspace_state(
            after.state_hash,
            step=turn.step,
            event=event,
            stagnation_limit=self.stagnation_limit,
        ):
            return AgentStepTransition(
                event=event,
                after=after,
                termination=AgentTermination(
                    success=False,
                    reason="workspace state cycle threshold reached",
                    steps=turn.step,
                ),
            )
        return AgentStepTransition(event=event, after=after)


@dataclass(frozen=True, slots=True)
class AgentVerificationTransitionHandler:
    verifier: VerificationPort
    workspace: Path
    commands: tuple[CommandSpec, ...]
    run_id: str
    deadline_monotonic: float
    verification_interval_mutations: int

    def finish(
        self,
        turn: AgentTurn,
        state: AgentRunState,
    ) -> AgentStepTransition:
        decision = turn.decision
        if decision is None or decision.kind != "finish":
            raise TypeError("final verification requires a parsed finish decision")
        state.final_summary = decision.summary
        if state.last_failed_verification_state == turn.before.state_hash:
            event = {
                "kind": "verification_rejected",
                "final": True,
                "workspace_state": turn.before.state_hash,
                "error": (
                    "Verification already failed on this unchanged workspace. "
                    "Inspect or modify the implementation before requesting "
                    "verification again."
                ),
            }
            state.events.append(event)
            return AgentStepTransition(event=event, after=turn.before)

        receipt = self._verify(turn.step, final=True, state=state)
        after = snapshot_workspace(self.workspace)
        event = verification_event(receipt, final=True)
        state.update_criterion_status(event)
        state.remember_verification_state(after.state_hash, turn.step)
        state.events.append(event)
        if not receipt.workspace_unchanged:
            termination = AgentTermination(
                success=False,
                reason="verification command mutated the workspace",
                steps=turn.step,
            )
        elif receipt.passed:
            termination = AgentTermination(
                success=True,
                reason="final verification passed",
                steps=turn.step,
            )
        else:
            state.last_failed_verification_state = after.state_hash
            state.mutations_since_verify = 0
            termination = None
        return AgentStepTransition(
            event=event,
            after=after,
            termination=termination,
        )

    def after_tool(
        self,
        turn: AgentTurn,
        state: AgentRunState,
        transition: AgentStepTransition,
    ) -> AgentStepTransition:
        if (
            not transition.event.get("mutated")
            or state.mutations_since_verify
            < self.verification_interval_mutations
        ):
            return transition

        receipt = self._verify(turn.step, final=False, state=state)
        state.mutations_since_verify = 0
        verification = verification_event(receipt, final=False)
        state.update_criterion_status(verification)
        state.remember_verification_state(transition.after.state_hash, turn.step)
        state.events.append(verification)
        transition.event["followup_verification"] = verification
        if not receipt.workspace_unchanged:
            return AgentStepTransition(
                event=transition.event,
                after=snapshot_workspace(self.workspace),
                termination=AgentTermination(
                    success=False,
                    reason="verification command mutated the workspace",
                    steps=turn.step,
                ),
            )
        if receipt.passed:
            state.last_failed_verification_state = None
        else:
            state.last_failed_verification_state = transition.after.state_hash
        return transition

    def _verify(
        self,
        step: int,
        *,
        final: bool,
        state: AgentRunState,
    ) -> VerificationReceipt:
        verification_run_id = (
            f"{self.run_id}-final-{step}"
            if final
            else f"{self.run_id}-intermediate-{step}"
        )
        receipt = self.verifier.verify(
            self.workspace,
            self.commands,
            run_id=verification_run_id,
            deadline_monotonic=self.deadline_monotonic,
        )
        state.verifications += 1
        state.verification_artifacts.append(
            receipt_reference(self.verifier, receipt.run_id)
        )
        return receipt


def verification_event(
    receipt: VerificationReceipt,
    *,
    final: bool,
) -> dict[str, Any]:
    commands: list[dict[str, object]] = []
    for command in receipt.commands:
        rendered: dict[str, object] = {
            "name": command.name,
            "criteria": list(command.criteria),
            "passed": command.passed,
            "timed_out": command.timed_out,
            "exit_code": command.exit_code,
            "stdout_path": command.stdout_path,
            "stderr_path": command.stderr_path,
            "failure_category": command.failure_category,
        }
        if not command.passed:
            if command.failure_category == "execution_error":
                rendered["feedback"] = (
                    "Verification could not complete its assertions normally. Inspect "
                    "syntax, imports, referenced names, and runtime-valid control flow, "
                    "then make a concrete repair before verification."
                )
            elif command.failure_category == "timeout":
                rendered["feedback"] = (
                    "Verification timed out. Inspect termination conditions and bounded "
                    "work, then make a concrete repair before verification."
                )
            else:
                rendered["feedback"] = (
                    "Verification failed. Inspect the current workspace, compare the "
                    "implementation with every acceptance criterion, and make a concrete "
                    "repair before requesting verification again."
                )
        commands.append(rendered)
    return {
        "kind": "verification",
        "final": final,
        "run_id": receipt.run_id,
        "passed": receipt.passed,
        "workspace_unchanged": receipt.workspace_unchanged,
        "criteria": receipt.to_dict()["criteria"],
        "commands": commands,
    }


def receipt_reference(
    verifier: VerificationPort,
    run_id: str,
) -> ArtifactRef:
    if not isinstance(verifier, VerificationEvidencePort):
        raise RuntimeError("verification port did not expose a receipt artifact")
    return verifier.receipt_reference(run_id)
