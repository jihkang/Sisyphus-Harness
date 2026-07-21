# Slice B: Control Outcome Authority

## Scope

This slice implements the `AttemptFinished -> Control adjudication -> TaskOutcome`
authority chain. It starts from merged revision
`77cd48e948bd5875dc1ff00128752d00ceb3225e` and intentionally excludes benchmark,
evolution, hidden-oracle, and task-graph changes.

## Implemented Boundaries

| Boundary | Implementation | Invariant |
| --- | --- | --- |
| Worker publication | `contracts/control/attempts.py::AttemptFinished`, `JobQueue.finish_attempt()` | no semantic success field; lease and attempt are fenced atomically |
| Attempt persistence | schema migration 2, `attempt_finished` | content digest, exact queue projection, immutable update/delete triggers |
| Control service | `services/control_outcomes.py::ControlTaskOutcomeService` | reloads authority and never consumes Agent success as evidence |
| Outcome persistence | `SQLiteTaskOutcomeAuthority` | one immutable outcome per job with contract/profile/evaluation snapshots; stale and conflicting publications fail |
| Final verification | `runtime.build_control_task_outcome_service()` | Docker transport is mandatory even for trusted Agent mode |
| Operator projection | `task-status`, `task-adjudicate` | execution, attempt lineage, and semantic outcome are separate JSON fields |

## State Semantics

| Projection | Values | Meaning |
| --- | --- | --- |
| `jobs.status` | queued, running, completed, failed | queue and Worker execution lifecycle only |
| `AttemptFinished.agent_result.success` | boolean | diagnostic report from the coding loop, never outcome authority |
| `TaskOutcome.decision` | passed, failed, indeterminate | Control-owned EvidenceContract result |

An Agent-reported failure may have `jobs.status=completed` and a later
`TaskOutcome.decision=passed`. That is expected: execution successfully produced
an immutable candidate, and independent evidence accepted it.

## Regression Gates

- expired attempt publication fails after lease reclaim;
- generic `queue-finish` completion cannot fabricate attempt authority;
- Agent false plus passing independent evidence produces a passed outcome;
- identical Control publication is idempotent;
- different Control inputs or a stale bundle/digest fail closed;
- direct SQL update/delete of attempt and outcome rows is rejected;
- Control composition remains Docker-backed in trusted Agent mode;
- CLI status renders `job`, `attempt_finished`, and `task_outcome` separately.

## Local Validation

- full suite: 389 discovered, 387 passed, 2 opt-in Docker tests skipped;
- branch coverage: 90.3%, above the 90.0% gate;
- opt-in real Docker suite: 2 passed, including the complete
  attempt-to-contained-verification-to-persisted-outcome path;
- Ruff 0.15.22: passed;
- Bandit 1.9.4 at medium/high severity: no findings;
- historical evidence manifest: 126 files verified with
  `source_matches_head=false`;
- frozen lock, compileall, offline sdist/wheel build, and isolated Python 3.14
  wheel import/CLI smoke: passed.

These are working-branch results. GitHub CI and merge evidence are still required
before SH-P0-002 is marked closed.

## Remaining Limits

This slice does not yet provide verifier-only hidden assets, admitted task graph
dispatch authority, Gap closure transactions, external signing/KMS, replicated
storage, or current 30.5B benchmark evidence. Those remain separate gates so a
local SQLite outcome is not overstated as full production control-plane proof.
The canonical IDs and executable exit conditions for these limits are maintained
in the [implementation debt register](../../status/implementation-debt.md).
