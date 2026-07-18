# ADR 0004: Authority, Queue, Policy, and Artifact Ownership

- Status: Accepted
- Date: 2026-07-18

## Context

The current Git common-directory tree combines SQLite queue state, run artifacts,
and policy authority. Multiple containers must not share unrestricted access to
that directory or write each other's state.

## Decision

Control is the only owner of external job state and policy activation. Services
publish append-only results and artifact references through defined ports.

| Owner | Data |
| --- | --- |
| Control | task/evolution job states, config and policy snapshots, approvals, active policy |
| Agent | traces, patches, workspace bundles, agent results |
| Verifier | command outputs and verification receipts |
| Evolve | baselines, observations, aggregates, candidates, evolution results |
| Artifact store | immutable bytes addressed by ID and digest |

The first Docker deployment may keep SQLite behind a single Control writer and a
filesystem artifact volume. Other services must use Control or artifact APIs rather
than opening the database. PostgreSQL and an object store are later adapters, not
prerequisites for the initial separation.

Queue messages snapshot the effective config and policy digests. A path or the
word `active` alone is insufficient execution provenance. Policy signing material
is mounted only into Control and is never available to Agent, Verifier, Evolve, or
the model server.

## Consequences

- Current direct CLI commands become compatibility clients that submit and wait.
- Queue and artifact transport remain behind ports so local tests can use in-process
  and filesystem adapters.
- Artifact retention and garbage collection require an explicit policy because
  append-only evidence is otherwise unbounded.
- Service terminal state must be idempotent and correlated to immutable request and
  result digests.
