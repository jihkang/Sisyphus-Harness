# Slice B Code Review

## Review Scope

Reviewed the complete Slice B diff from merged `main` revision `77cd48e` across
wire contracts, SQLite migration and transactions, queue/Worker behavior,
Control ports/services/adapters, CLI projection, tests, and documentation.

## Findings Resolved Before Commit

| Severity | Finding | Resolution |
| --- | --- | --- |
| High | an idempotent Control retry could return an existing outcome without rechecking current attempt authority | every retry reloads `AttemptFinished` and re-enters the fenced publish transaction |
| High | the initial outcome stored only evaluation digests, so removal of operator input files would make the decision incomplete | outcome now snapshots the exact contract, profile, and complete `ContractEvaluation` |
| High | the initial relational foreign key bound only job/attempt, not the attempt digest | the composite foreign key now includes `attempt_digest`; a mismatch fails at SQLite level |
| High | Control trusted identity fields from an adjudication port without independently rechecking its exact request bundle/profile/run/result binding | the port result validates its internal bindings and Control rechecks them against the authoritative attempt before publication |
| Medium | the attempt transaction accepted any leased low-level job kind | both Worker publication and Control publication require the authoritative job to be `coding-agent` |
| Medium | attempt parsing existed in both queue and Control infrastructure | removed the queue reader; Control authority is the single canonical persisted-attempt reader |
| Medium | evaluation-to-outcome decision mapping existed in both the contract and Control service | `TaskOutcomeDecision.from_evaluation()` now owns the domain mapping |
| Medium | legacy, Worker, and outcome contracts had grown into one 445-line module | split the stable import path into `control/legacy.py`, `attempts.py`, `outcomes.py`, and shared validation |
| Medium | persisted payload parsing did not compare duplicated SQL columns with content digests | Control reads now cross-check identity and digest columns against strict parsed payloads |

## Verified Invariants

- Worker source contains no `TaskOutcome` writer or authority port.
- only `infra/control_outcomes.py` contains the `task_outcomes` insert statement.
- Control application service imports contracts and ports, not queue/database/infra.
- generic low-level queue completion cannot create `AttemptFinished` authority.
- an expired Worker cannot publish after another attempt reclaims the lease.
- Agent-reported false can still produce a passed outcome only through independent
  contained evidence.
- attempt and outcome rows reject update/delete, and a digest-mismatched foreign
  key insert is rejected.
- exact retries are idempotent and revalidate authority; changed run, producer,
  profile, contract, attempt, or outcome inputs fail closed.

## Residual Risks

1. [`SH-CTRL-001`](../../status/implementation-debt.md) - concurrent first-time
   adjudications do not have a dedicated Control lease.
   Docker artifact publication and the unique outcome row prevent double
   authority, but one caller may fail and require a retry.
2. [`SH-GRAPH-001`](../../status/implementation-debt.md) - `TaskOutcome` is bound
   to the queue job and attempt, but admitted TaskBasis,
   TaskGraph dispatch, and source-grounding digests do not exist yet.
3. [`SH-TRUST-001`](../../status/implementation-debt.md) - SQLite and filesystem
   evidence share the local OS-account trust boundary; no
   external append-only ledger, KMS signature, revocation, or replication exists.
4. [`SH-VERIFY-001`](../../status/implementation-debt.md) - verifier image
   identity and hidden-oracle assets are not yet part of the
   outcome contract. That work remains Slice C/F.
5. [`SH-IO-001`](../../status/implementation-debt.md) - `task-adjudicate` reads
   operator JSON from repository-contained paths but does
   not yet use a generic race-resistant shared file-open primitive.

No residual item above permits Worker or Agent code to publish semantic success.
They are follow-up hardening and authority-expansion work, not reasons to merge
execution state back into task state.
