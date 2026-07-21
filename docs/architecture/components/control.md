# Control Boundary

## Responsibility

Control owns authoritative task lifecycle decisions. It admits work, records
execution lineage, independently requests final verification, evaluates the
immutable evidence contract, publishes one semantic task outcome, and owns
operator approval and active policy state.

## Owned Authority

- queue state, leases, attempt fencing, and current attempt selection;
- immutable `AttemptFinished` and `TaskOutcome` persistence;
- final verification request and evidence-contract evaluation;
- approval records, active policy, and local signing key;
- future admitted task graph and closure transactions.

## Forbidden Authority

Control must not execute candidate code in its own process, let Worker success
stand in for task success, let Evolve self-activate, accept Agent-authored
verification facts, or silently rebind an outcome to a new attempt, profile,
contract, or source revision.

## Current Implementation

`src/sisyphus_harness/queue.py` and `src/sisyphus_harness/database.py` implement
the local SQLite queue and lease transitions. `src/sisyphus_harness/worker.py`
publishes attempt lineage. `src/sisyphus_harness/services/control_outcomes.py`
independently verifies the current output bundle, reads authoritative evidence,
evaluates the contract, and asks
`src/sisyphus_harness/infra/control_outcomes.py` to publish an immutable outcome.

`src/sisyphus_harness/policy.py` owns local HMAC approval and active-policy
artifacts. These responsibilities are logically Control-owned but still share
the package and local OS identity with other components. The knowledge index is
candidate-only and cannot dispatch or close tasks.

## Contracts

| Direction | Contract or port | Meaning |
| --- | --- | --- |
| Inbound | typed coding job and idempotency key | admitted execution request |
| Execution evidence | `AttemptFinished` | exact attempt and input/output bundle lineage |
| Verification dependency | `VerificationServicePort` | independent final verification |
| Evaluation dependency | `EvidenceContractAdjudicationPort` | pure three-valued contract evaluation |
| Authority | `TaskOutcomeAuthorityPort` | fenced immutable publication |
| Result | `TaskOutcome` | semantic success, failure, or indeterminate outcome |

## Target Boundary

Only Control receives queue credentials, policy signing material, active policy
write access, and authoritative task-graph state. A fenced adjudication lease
prevents concurrent first-publication races. Authenticated service transports
bind actor, request, attempt, bundle, profile, contract, and deadline identities.
Repository governance protects authority code with current-head CI and required
human review.

## Open Debt And Evidence

- `SH-CTRL-001`: dedicated adjudication lease is not implemented.
- `SH-GRAPH-001`: admitted Claim/Gap/TaskBasis/TaskGraph authority is not implemented.
- `SH-TRUST-001`: external identity, ledger, revocation, and recovery are out of scope today.
- `SH-GOV-001`: protected-branch and designated-review evidence remains open.

Primary regression suites are `tests/test_control_outcomes.py`,
`tests/test_evidence_adjudication.py`, `tests/test_queue.py`,
`tests/test_database.py`, and `tests/test_policy.py`.
