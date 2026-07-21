# Findings Register

## Critical

| ID | Finding | Required invariant | State |
| --- | --- | --- | --- |
| SH-P0-001 | Agent could rewrite verifier inputs | model writes are restricted to an operator-owned positive allowlist | Closed by merged PR #8 |
| SH-P0-002 | Control adjudication is shadow-only | only Control can publish semantic TaskOutcome | Locally implemented in Slice B; CI/merge pending |
| SH-P0-003 | Qwen evidence was presented without a current-revision classification | every measurement declares historical or current-release scope | Historical manifest verification passed |
| SH-P0-004 | Review completed after oversized merges | protected main requires current CI and review | Open repository setting |
| SH-P0-005 | strict response schema used a forbidden union root | strict schema has one object root and explicit fallback mode | Merged; live endpoint validation pending |
| SH-P0-006 | in-process verifier executed candidate code with host authority | untrusted mode uses bundle-backed Docker verification by default | Closed by merged PR #8 and CI container probe |

## Structural And Operational

P1 findings remain grouped by ownership rather than by file size:

1. Interface decomposition: CLI parser, handlers, and composition.
2. Runtime decomposition: Agent loop, Worker lease/attempt execution, Verifier process lifecycle.
3. Shared primitives: atomic I/O, digests, identifiers, clocks, and strict JSON.
4. Authority: AttemptFinished, fenced Control adjudication, TaskOutcome persistence.
5. Operations: type gate, platform matrix, artifact quota/retention, supply-chain pinning.

## Benchmark And Evidence

The committed 30.5B bundle is explicitly historical and has `train n=4` and
`holdout n=2`. It is smoke evidence, not a generalized performance claim. A
current-release claim requires repeated trials, dispersion, independent holdout,
model/config/dataset/policy/image/source digests, and the installed release wheel.
