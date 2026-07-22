# Responsibility Decomposition Final Review

- Review date: 2026-07-22
- Reviewed revision: `main@06cce47ef237e736f88639798d999f8933f9856d`
- Scope: Docker verifier, local Agent, CLI, Knowledge, and WorkspaceTools
- Delivery status: all five independent implementation PRs merged with exact-head CI

## Final Finding

No unresolved High finding, facade-level behavioral regression, or confirmed
god class remains in the five targeted surfaces. This is a scoped conclusion,
not a claim that every large production module should remain unchanged.

| Slice | Before | Current facade | Delivery evidence |
| --- | ---: | ---: | --- |
| Docker verifier | 952 class lines | 311 class lines | PR #13, CI `29915544947` |
| Local coding Agent | 738 class lines | 119 class lines | PR #15, CI `29917555768` |
| CLI | 731 module lines | 43 module lines | PR #17, CI `29919408040` |
| Knowledge graph/index | 502/515 class lines | 85/64 module lines | PR #19, CI `29921307245` |
| Workspace tools | 441 class lines | 90 module lines | PR #21, CI `29923115539` |

Each facade now owns composition and its public boundary. Operational loops,
storage mechanics, protocol parsing, rendering, traversal, or file mutation
live in bounded collaborators with architecture guards that prevent the
original concentration from returning.

## Whole-Codebase Scan

An AST scan of every production class and module at the reviewed revision
identified the following largest remaining classes:

| Class | Lines | Methods | Assessment |
| --- | ---: | ---: | --- |
| `FilesystemVerifierAssetBundleStore` | 369 | 9 | residual watchlist; creation, authoritative load, materialization, and integrity all belong to one no-symlink verifier-asset CAS boundary, but future changes should split traversal mechanics behind the same store port |
| `DockerVerifierTransport` | 311 | 19 | accepted facade; runtime, host verification, bundle view, and evidence publication are already extracted and guarded |
| `FilesystemWorkspaceBundleStore` | 295 | 7 | cohesive bundle storage adapter; archive creation/load/materialization share one immutable bundle contract |
| `JobQueue` | 294 | 9 | cohesive transactional lease repository; queue and fenced attempt transitions remain one SQLite authority |
| `BoundedVerifier` | 282 | 5 | cohesive trusted compatibility verifier; Docker process/runtime responsibilities are separately owned on the default path |

The scan also finds contract modules over 1,000 lines. Their size is dominated
by strict versioned dataclass validation and parsing, not orchestration or mixed
authority, so line count alone is not evidence of a god class. They remain
maintainability watchpoints under `SH-TEST-001` and `SH-TYPE-001` rather than
decomposition findings.

## Remaining Debt

Responsibility decomposition did not close these broader product/security
items:

- `SH-ARCH-002`: authenticated Agent/Verifier/Evolve/Control transports and
  in-process versus Compose parity are incomplete;
- `SH-IO-001`: one repository-wide no-follow, stable-stat bounded file API is
  incomplete;
- `SH-TEST-001`: several large test suites still need responsibility-based
  splitting;
- `SH-EVOLVE-001`, `SH-BENCH-001`, and `SH-EVIDENCE-001`: Hermes lifecycle,
  repeated 30.5B evidence, and current-release manifest work remain open;
- `SH-GRAPH-001`, `SH-CTRL-001`, `SH-GOV-001`, and supply/retention/type debt
  remain exactly as listed in the canonical implementation debt register.

These are real remaining work items, but none requires restoring mixed
responsibility to the five facades reviewed here.

## Verification Summary

The final WorkspaceTools slice retained all original facade behavior tests,
added identity/signature and architecture guards, passed 456 tests with 90.6%
branch coverage, produced an exact base/current tool projection digest of
`83bc08af2c91359980fdf53f40a8a9e97ff5aa4880f6abc07358bcffdac786a9`,
and passed all five jobs in CI run `29923115539`. Earlier slice reviews contain
their corresponding parity evidence and exact implementation/merge revisions.
