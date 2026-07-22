from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import time

from .agent_artifacts import AgentRunRecorder, AgentTurn
from .agent_context import AgentPromptRenderer
from .agent_state import AgentRunState, AgentTermination
from .agent_transitions import (
    AgentToolTransitionHandler,
    AgentVerificationTransitionHandler,
)
from .config import AgentLimits
from .contracts.agent import AgentResult, AgentTask
from .contracts.policy import CadencePolicy
from .deadline import DeadlineExceeded, MonotonicDeadline
from .protocol import ProtocolError, parse_agent_decision
from .provider import (
    ChatProvider,
    DeadlineChatProvider,
    ProviderError,
)
from .workspace import snapshot_workspace


@dataclass(frozen=True, slots=True)
class AgentRunLoop:
    provider: ChatProvider
    task: AgentTask
    limits: AgentLimits
    cadence: CadencePolicy
    workspace: Path
    deadline: MonotonicDeadline
    renderer: AgentPromptRenderer
    recorder: AgentRunRecorder
    observe_workspace: Callable[[], dict[str, object]]
    tool_transitions: AgentToolTransitionHandler
    verification_transitions: AgentVerificationTransitionHandler
    clock: Callable[[], float] = time.monotonic

    def run(self, state: AgentRunState) -> AgentResult:
        for step in range(1, self.limits.max_steps + 1):
            if self.deadline.expired():
                return self.recorder.finish(
                    state,
                    AgentTermination(
                        success=False,
                        reason="runtime budget exhausted",
                        steps=step - 1,
                    ),
                )
            termination = self._run_step(step, state)
            if termination is not None:
                return self.recorder.finish(state, termination)
        return self.recorder.finish(
            state,
            AgentTermination(
                success=False,
                reason="step budget exhausted",
                steps=self.limits.max_steps,
            ),
        )

    def _run_step(
        self,
        step: int,
        state: AgentRunState,
    ) -> AgentTermination | None:
        if state.should_compact(
            step,
            self.cadence,
            max_compactions=self.limits.max_compactions,
        ):
            payload = state.compact(step, self.cadence)
            self.recorder.write_compaction(state.compactions, payload)

        observation = None
        if step == 1 or step % self.cadence.observation_interval_steps == 0:
            observation = self.observe_workspace()
        reflection_due = step % self.cadence.reflection_interval_steps == 0
        messages = self.renderer.messages(
            self.task,
            state.events,
            state.compact_summary,
            observation,
            reflection_due,
            state.known_file_hashes,
            state.working_file,
            state.criterion_status,
        )
        before = snapshot_workspace(self.workspace)
        model_started = self.clock()
        try:
            if isinstance(self.provider, DeadlineChatProvider):
                response = self.provider.complete_with_timeout(
                    messages,
                    timeout_seconds=self.deadline.remaining(),
                )
            else:
                response = self.provider.complete(messages)
            if self.deadline.expired():
                raise DeadlineExceeded("global execution deadline exceeded")
        except DeadlineExceeded:
            return AgentTermination(
                success=False,
                reason="runtime budget exhausted",
                steps=step - 1,
            )
        except ProviderError as exc:
            turn = AgentTurn(
                step=step,
                before=before,
                messages=messages,
                model_content="",
                model_duration_ms=round((self.clock() - model_started) * 1000),
                decision=None,
                prompt_tokens=None,
                completion_tokens=None,
            )
            event = {
                "kind": "provider_error",
                "step": step,
                "error": str(exc),
            }
            self.recorder.write_step(turn, after=before, event=event)
            return AgentTermination(
                success=False,
                reason=f"provider failure: {exc}",
                steps=step - 1,
            )

        model_duration_ms = round((self.clock() - model_started) * 1000)
        try:
            decision = parse_agent_decision(response.content)
        except ProtocolError as exc:
            state.protocol_errors += 1
            event = {
                "kind": "protocol_error",
                "step": step,
                "error": str(exc),
                "model_content": response.content,
            }
            state.events.append(event)
            turn = AgentTurn(
                step=step,
                before=before,
                messages=messages,
                model_content=response.content,
                model_duration_ms=model_duration_ms,
                decision=None,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
            )
            self.recorder.write_step(turn, after=before, event=event)
            if state.protocol_errors > self.limits.max_protocol_errors:
                return AgentTermination(
                    success=False,
                    reason="model protocol error budget exhausted",
                    steps=step,
                )
            return None

        turn = AgentTurn(
            step=step,
            before=before,
            messages=messages,
            model_content=response.content,
            model_duration_ms=model_duration_ms,
            decision=decision,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
        )
        fingerprint, stagnant = state.observe_decision(
            decision,
            stagnation_limit=self.cadence.stagnation_limit,
        )
        if stagnant:
            event = {
                "kind": "stagnation",
                "step": step,
                "fingerprint": fingerprint,
                "repeat_count": state.repeated_fingerprint,
            }
            self.recorder.write_step(turn, after=before, event=event)
            return AgentTermination(
                success=False,
                reason="stagnation threshold reached",
                steps=step,
            )

        if decision.kind == "finish":
            transition = self.verification_transitions.finish(turn, state)
        else:
            transition = self.tool_transitions.execute(turn, state)
            if transition.termination is None:
                transition = self.verification_transitions.after_tool(
                    turn,
                    state,
                    transition,
                )
        self.recorder.write_step(
            turn,
            after=transition.after,
            event=transition.event,
        )
        return transition.termination
