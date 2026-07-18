# ADR 0003: Verification Is an Independent Authority

- Status: Accepted
- Date: 2026-07-18

## Context

Agent needs intermediate and final verifier feedback to repair a task, while
Evolve needs trustworthy criterion outcomes to score a candidate. Neither service
may manufacture or reinterpret a passing verification result.

## Decision

Verifier alone executes `CommandSpec` and creates `VerificationReceipt`. Agent may
derive its run state from a receipt, and Evolve may derive an observation from a
receipt, but neither may alter command or criterion results.

Every service-mode receipt binds:

- verification request and workspace bundle digests;
- command profile and acceptance criteria;
- executable provenance;
- stdout and stderr artifact references;
- before and after workspace state hashes;
- timeout, exit, and mutation status;
- receipt schema version and digest.

Verifier runs non-root with no network, a read-only root filesystem, dropped Linux
capabilities, process and resource limits, and an ephemeral writable workspace.
The policy signing key and control database are never mounted into Verifier.

## Consequences

- Agent depends on `VerificationPort`, not `BoundedVerifier`.
- Evolve consumes receipt evidence through `VerificationEvidencePort`; a direct
  Verifier call is reserved for an explicit independent re-verification use case.
- Existing in-process verification remains the compatibility adapter until the
  sandboxed worker passes parity tests.
- Verification failure is data, while transport failure is a retryable service
  error with a separate error contract.
