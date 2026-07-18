# ADR 0001: Agent, Verifier, and Evolve Service Boundaries

- Status: Accepted
- Date: 2026-07-18

## Context

The current process composes `LocalCodingAgent`, `BoundedVerifier`, benchmark
evaluation, and GEPA evolution in one Python distribution. Their responsibilities,
trust levels, resource profiles, and scaling behavior are different. Container
separation must not create circular implementation dependencies or allow evolution
to acquire verification or activation authority.

## Decision

The target system has three independent execution services and one supporting
control plane.

- Agent owns model interaction, bounded file tools, context management, workspace
  mutation, and the repair loop.
- Verifier is the sole authority for executing operator commands and issuing
  verification receipts.
- Evolve owns datasets, GEPA, scoring, aggregation, and proposed/rejected candidate
  decisions.
- Control owns external APIs, job state, operator approval, and active policy.

Dependency direction is fixed as follows:

```text
Verifier -> Contracts
Agent -> Contracts + VerificationPort
Evolve -> Contracts + AgentRunPort + VerificationEvidencePort
```

Evolve may depend on verified Agent outcomes at runtime, but it must not import
Agent or Verifier implementations. Verifier must not import Agent or Evolve.

Shared code is restricted to versioned wire contracts and ports. Business logic,
persistence adapters, and service entrypoints are not shared through the contracts
package.

## Consequences

- Existing import paths remain compatibility aliases during migration.
- Ports receive in-process adapters before any transport adapter is introduced.
- Long-running service calls may later use queue-backed request/reply without
  changing application logic.
- Dependency tests fail CI when the direction is violated.
