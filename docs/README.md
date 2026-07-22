# Documentation Map

This index separates durable architecture, accepted decisions, dated reviews,
and executable delivery plans. Runtime code and regression tests remain the
authority when a dated document describes an older revision.

## Current Architecture

- [Structured architecture map](architecture/README.md): responsibility-based
  Agent, Verifier, Evolve, Control, trust, artifact, and pipeline views.
- [Architecture](architecture.md): short component and authority overview.
- [Architecture and Data Pipeline](architecture-and-data-pipeline.md): detailed
  module map, trust boundaries, storage, and direct/queue/benchmark/evolution
  flows.
- [Evolution](evolution.md): Hermes-style GEPA policy evolution and promotion.
- [Security Policy](../SECURITY.md): supported scope, reporting, and operational
  requirements.

## Decisions

- [ADR 0001](adr/0001-service-boundaries.md): service boundaries.
- [ADR 0002](adr/0002-workspace-bundle.md): immutable workspace bundles.
- [ADR 0003](adr/0003-verification-authority.md): verification authority.
- [ADR 0004](adr/0004-authority-and-artifact-ownership.md): control and artifact ownership.
- [ADR 0005](adr/0005-default-deny-execution.md): default-deny writes and contained verification.
- [ADR 0006](adr/0006-control-owned-task-outcomes.md): execution lineage and Control-owned semantic outcomes.
- [ADR 0007](adr/0007-verifier-asset-and-image-binding.md): verifier asset integrity and immutable image identity.
- [ADR 0008](adr/0008-host-owned-verification-evidence.md): per-command containers and host-owned verification evidence.

## Current Status

- [Status map](status/README.md): authority order and update contract for living
  project status.
- [Canonical conformance model](status/conformance-model.md): `GREEN`, `AMBER`,
  `RED`, and `GRAY` definitions and promotion rules.
- [Implementation debt register](status/implementation-debt.md): stable debt IDs,
  current facts, dependency slices, and executable exit conditions.

## Reviews

- [2026-07-21 strict review](reviews/2026-07-21/README.md): active findings,
  remediation slices, and closure gates.
- [2026-07-21 verification gates](reviews/2026-07-21/verification-gates.md):
  executable proof required before a finding is closed.
- [2026-07-21 Stage 0 validation](reviews/2026-07-21/stage-0-validation.md):
  local results and remaining external proof.
- [2026-07-21 Slice B Control authority](reviews/2026-07-21/stage-b-control-authority.md):
  AttemptFinished fencing and TaskOutcome publication.
- [2026-07-21 Slice B code review](reviews/2026-07-21/stage-b-code-review.md):
  resolved findings and remaining authority risks.
- [2026-07-21 Slice C verifier integrity review](reviews/2026-07-21/stage-c-verifier-integrity.md):
  asset/image binding, adversarial tests, and confidentiality limit.
- [2026-07-21 code review](code-review-2026-07-21.md): prior merged-scope review.
- [2026-07-22 verifier command isolation review](reviews/2026-07-22/verifier-command-isolation.md): post-implementation authority and failure-path review.
- [2026-07-22 Docker verifier decomposition review](reviews/2026-07-22/docker-verifier-decomposition.md): responsibility split and behavior-parity evidence.
- [2026-07-18 architecture conformance](architecture-conformance-review-2026-07-18.md):
  historical code/document comparison.

## Delivery Plans

- [Implementation plans](plans/): current architecture and security changes with
  explicit invariants, migration steps, and completion gates.
- [Responsibility decomposition plan](plans/2026-07-22-responsibility-decomposition.md): independent Docker, Agent, CLI, Knowledge, and workspace-tool refactors.
- [Strict 95-point plan](sisyphus_harness_strict_review_95_plan_2026-07-21.md):
  detailed source review and target score.
- [Execution plan](execution-plan.md): model benchmark, evolution, evidence, and release sequence.
- [Task graph evolution plan](task-graph-evolution-plan.md): evidence-grounded task authority design.

Date-stamped reviews never silently become current-release evidence. Their
headers identify the reviewed revision, and their status tables must point to
the current implementation or an explicit open gate.
