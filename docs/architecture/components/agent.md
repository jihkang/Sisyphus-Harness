# Agent Boundary

## Responsibility

Agent turns an operator task and active candidate policy into bounded model
decisions. It manages prompts, deterministic compaction, tool observations,
stagnation detection, deadlines, and intermediate verifier feedback.

## Owned Authority

- model request and response orchestration for one run;
- invocation of the allowlisted workspace tools;
- context compaction and retry cadence;
- diagnostic `AgentResult` and step artifacts;
- proposal of an attempt output through the Worker path.

## Forbidden Authority

Agent must not own queue transitions, immutable `AttemptFinished` publication,
final `TaskOutcome`, verification commands or oracle assets, policy approval,
policy activation, signing keys, or the Control database. `finish` is a request
for verification, not a success assertion.

## Current Implementation

The loop is implemented in `src/sisyphus_harness/agent.py`. Composition in
`src/sisyphus_harness/runtime.py` selects the contained bundle verifier by
default and the explicit trusted in-process adapter only when configured.
`src/sisyphus_harness/tools.py` applies the positive write allowlist and path
checks. `src/sisyphus_harness/compaction.py` owns deterministic context
reduction.

Queued runs are initiated by `src/sisyphus_harness/worker.py`. The Worker
materializes an immutable input bundle into an attempt-specific workspace,
runs Agent, captures an output bundle, and publishes execution lineage. Control
later determines semantic success.

## Contracts

| Direction | Contract or port | Meaning |
| --- | --- | --- |
| Inbound | `AgentTask`, `CandidatePolicy` | operator intent and bounded strategy |
| Outbound | `ChatProvider` | model completion request |
| Outbound | `VerificationPort` | intermediate or requested final evidence |
| Outbound | workspace tools | bounded reads and writes |
| Result | `AgentResult` | diagnostic run result, not task authority |
| Queue result | `AttemptFinished` | exact input/output lineage published by Worker |

## Target Boundary

Agent becomes an independently deployable service only after its transport
authenticates the admitted task, input bundle, policy, deadline, and attempt
identity. It must receive no Control signing material or verifier-only asset
store. In-process and Compose paths must produce equivalent wire contracts.

## Open Debt And Evidence

- `SH-ARCH-002`: process and transport separation is incomplete.
- `SH-IO-001`: not every filesystem read uses the shared race-resistant API.
- `SH-COMPAT-001`: legacy result aliases still preserve old imports.

Primary regression suites are `tests/test_agent.py`, `tests/test_tools.py`,
`tests/test_compaction.py`, `tests/test_runtime.py`, and `tests/test_worker.py`.
