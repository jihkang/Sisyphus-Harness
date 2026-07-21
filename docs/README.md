# Documentation Map

This index separates durable architecture, accepted decisions, dated reviews,
and executable delivery plans. Runtime code and regression tests remain the
authority when a dated document describes an older revision.

## Current Architecture

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

## Reviews

- [2026-07-21 strict review](reviews/2026-07-21/README.md): active findings,
  remediation slices, and closure gates.
- [2026-07-21 verification gates](reviews/2026-07-21/verification-gates.md):
  executable proof required before a finding is closed.
- [2026-07-21 Stage 0 validation](reviews/2026-07-21/stage-0-validation.md):
  local results and remaining external proof.
- [2026-07-21 code review](code-review-2026-07-21.md): prior merged-scope review.
- [2026-07-18 architecture conformance](architecture-conformance-review-2026-07-18.md):
  historical code/document comparison.

## Delivery Plans

- [Strict 95-point plan](sisyphus_harness_strict_review_95_plan_2026-07-21.md):
  detailed source review and target score.
- [Execution plan](execution-plan.md): model benchmark, evolution, evidence, and release sequence.
- [Task graph evolution plan](task-graph-evolution-plan.md): evidence-grounded task authority design.

Date-stamped reviews never silently become current-release evidence. Their
headers identify the reviewed revision, and their status tables must point to
the current implementation or an explicit open gate.
