# Evolve Boundary

## Responsibility

Evolve proposes bounded candidate-policy changes, evaluates them through actual
Agent rollouts and Verifier evidence, compares train and holdout behavior, and
returns a proposal for operator review. It does not activate its own result.

## Owned Authority

- candidate proposal lifecycle within the declared evolvable surface;
- trial scheduling and aggregate statistics;
- train and holdout comparison;
- evolution artifacts and recommendation output.

## Forbidden Authority

Evolve must not weaken immutable safety settings, fabricate verifier evidence,
write `TaskOutcome`, own the queue or Control database, sign approvals, or change
the active policy. Evaluation completion is not activation.

## Current Implementation

`src/sisyphus_harness/evolution.py` coordinates baseline evaluation, a GEPA
optimizer adapter, candidate re-evaluation, and promotion gates. The evaluator
uses `src/sisyphus_harness/benchmarks.py`, whose default compatibility factory is
still in process. `src/sisyphus_harness/policy.py` separately records operator
approval and activation using the local authority key.

GEPA currently carries optimizer and orchestration meaning. A separate
Hermes-backed evolution lifecycle has not been implemented. Current 30.5B model
evidence is historical smoke evidence rather than a repeated, current-release
improvement claim.

## Contracts

| Direction | Contract or port | Meaning |
| --- | --- | --- |
| Inbound | dataset, seed `CandidatePolicy`, evolution settings | frozen experiment inputs |
| Dependency | `AgentRunFactoryPort` / `AgentRunPort` | coding rollouts |
| Dependency | `VerificationEvidencePort` | authoritative scoring evidence |
| Optimizer | GEPA adapter | candidate search, not lifecycle authority |
| Result | `EvolutionResult` | proposal and measurements for operator review |

## Target Boundary

Hermes owns bounded evolution cadence, memory, and proposal lifecycle. GEPA is a
replaceable optimizer adapter. Both depend on Agent and Verifier ports and
receive immutable dataset, model, config, and policy digests. Repeated trials
report dispersion and predeclared gates. Control alone signs and activates an
accepted candidate.

## Open Debt And Evidence

- `SH-EVOLVE-001`: Hermes lifecycle and GEPA optimizer responsibilities are not split.
- `SH-BENCH-001`: repeated 30.5B train/holdout improvement is not proven.
- `SH-EVIDENCE-001`: release evidence does not bind all experiment inputs and outputs.
- `SH-ARCH-002`: Compose-equivalent service transport is incomplete.

Primary regression suites are `tests/test_evolution.py`,
`tests/test_gepa_integration.py`, and `tests/test_benchmarks.py`.
