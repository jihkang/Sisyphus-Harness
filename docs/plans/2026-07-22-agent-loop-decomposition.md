# Local Coding Agent Loop Decomposition Plan

- Date: 2026-07-22
- Status: Complete
- Base revision: `main@fbe3adf`
- Parent plan: [responsibility decomposition](2026-07-22-responsibility-decomposition.md)
- Related debt: `SH-ARCH-002`, `SH-IO-001`, `SH-TEST-001`

## 1. Verified Problem

`LocalCodingAgent` spans 738 lines and `run()` alone spans 569. The method owns
run initialization, mutable loop state, context rendering, provider invocation,
protocol accounting, compaction, decision stagnation, workspace cycle
detection, tool transitions, final and intermediate verification, artifact
projection, and result construction.

The behavior is well covered, but changing one transition requires reviewing
the whole loop and every early return. The goal is to reduce responsibility
concentration without changing model prompts, tool permissions, verification
authority, artifact schemas, error categories, counters, deadlines, or public
imports.

## 2. Required Invariants

1. `sisyphus_harness.agent.LocalCodingAgent`, `AgentTask`, and the public
   constructor/run signatures remain import-compatible.
2. The exact safety prompt, JSON context keys, verbatim working-file message,
   event shapes, artifact schema versions, run IDs, and result reasons remain
   unchanged.
3. The global monotonic deadline still bounds provider, tool, and verifier work;
   a provider returning after expiry cannot gain another transition.
4. A model cannot invoke verification directly. A `finish` decision requests
   host verification, and repeated failed verification on an unchanged tree is
   rejected without another verifier call.
5. Tool mutation reports are checked against observed workspace state. Tool
   errors that mutate terminate immediately.
6. Decision stagnation and workspace-state cycles retain their existing
   thresholds, counters, feedback, and criterion-aware state keys.
7. Intermediate and final verification keep the same receipt references,
   criterion status updates, mutation failure, counters, and artifact order.
8. Existing Agent facade tests remain characterization tests; private prompt
   compatibility is retained through a thin delegate.

## 3. Target Responsibilities

### `agent_context.py`

- own the immutable safety prompt and deterministic prompt rendering;
- own bounded working-file projection and known-hash updates;
- own workspace observation rendering;
- contain no provider, verifier, artifact, or lifecycle authority.

### `agent_state.py`

- own mutable counters and event history for one run;
- own compaction eligibility, semantic decision fingerprints, criterion-aware
  workspace visit tracking, and verification-state updates;
- expose deterministic transitions without filesystem or provider IO.

### `agent_artifacts.py`

- own metadata, step, compaction, and final-result projections onto
  `AgentRunStore`;
- preserve all current schema versions and field values;
- snapshot the final workspace only when constructing `AgentResult`.

### `agent_transitions.py`

- `AgentToolTransitionHandler` owns tool invocation, mutation consistency,
  working-file updates, and workspace-cycle termination;
- `AgentVerificationTransitionHandler` owns final/intermediate verifier calls,
  receipt references, criterion updates, and verification-derived termination;
- neither handler owns provider invocation or the outer step budget.

### `agent_loop.py`

- own provider invocation, protocol parsing/error budget, compaction timing,
  and the ordered dispatch of tool versus finish transitions;
- return only through `AgentRunRecorder.finish()`;
- contain no low-level file tool or verification implementation.

### `agent.py`

- retain the public facade, validate criterion coverage, construct the deadline,
  recorder, tools, state, renderer, and transition handlers, then delegate;
- retain `_messages()` only as an import/test compatibility delegate;
- contain no full decision loop or artifact serialization implementation.

## 4. Implementation Sequence

1. Add state and immutable transition/result records with unit-testable methods.
2. Move prompt and working-file projection verbatim; keep a facade delegate and
   run the existing prompt-content test.
3. Move metadata/step/final artifact projection verbatim and compare parsed
   artifact trees through existing successful, protocol-error, provider-error,
   cycle, and mutation tests.
4. Extract tool and verification handlers, preserving append/write order and
   every early termination reason.
5. Replace the original loop with `AgentRunLoop` orchestration and reduce
   `LocalCodingAgent` below 200 class lines and every new concrete class below
   325 lines.
6. Add an AST architecture guard for facade size, forbidden moved imports, and
   required collaborators.
7. Update current architecture, active debt, the parent plan, and a dated code
   review.

## 5. Regression Gates

Focused gates:

- all `tests.test_agent`, protocol, tools, verifier, in-process adapter, runtime,
  worker, benchmark, CLI, and architecture tests;
- prompt JSON and verbatim file-content assertions;
- successful repair, repeated failed finish, protocol/provider errors,
  decision stagnation, workspace oscillation, deadline, mutation cadence, and
  verifier-mutation cases;
- artifact count, schema, event ordering, reason, and counter assertions.

Delivery gates are the parent plan gates: complete branch coverage at least
90.0%, Ruff, Bandit, compileall, lock, Compose, evidence manifest, GEPA,
offline build/install, current-head CI, squash merge, refreshed `main`, and a
merge-evidence record.

## 6. Non-goals

- changing the model protocol, tools, prompts, cadence policy, or compaction
  algorithm;
- adding Hermes evolution behavior;
- changing verifier containment or task outcome authority;
- converting mutable run state into a persistent workflow engine;
- splitting the large test files in the same PR.

## 7. Rollback

No wire or artifact migration is introduced. The PR is independently
revertible to the previous `LocalCodingAgent` implementation, and existing run
artifacts remain readable because every schema and path stays unchanged.

## 8. Implementation Result

`LocalCodingAgent` is now a 119-line facade with a 72-line `run()` method. The
loop, prompt/context, mutable state, tool/verification transitions, and artifact
projection are separately owned, and every extracted concrete class remains
under 325 lines. AST comparison against `main@fbe3adf` confirms identical public
constructor, `run()`, and compatibility `_messages()` signatures and retains
every non-empty operational string constant.

Local validation passed 444 tests with three opt-in Docker skips and 90.5%
branch coverage. Ruff, Bandit medium/high, lock, compileall, Compose parsing,
historical evidence verification, GEPA integration, offline source/wheel build,
isolated wheel install, installed CLI/import probes, and all three rebuilt-image
Docker boundary tests passed. A deterministic repair scenario also produced an
equal parsed 11-artifact tree on `main@fbe3adf` and the extracted implementation
after normalizing timestamps, durations, absolute temporary paths, and their
derived digests.

Delivery completed through PR #15. Implementation commit
`d0d0c863818f267bc4bb193adcd880cccc8c76bc` passed all five jobs in CI run
`29917555768` and squash-merged as
`59f178e8673028305cf1ac5d02dab1fc4920ac3b`. Local `main` was fetched and
fast-forwarded to that exact merge revision before this closure record.
