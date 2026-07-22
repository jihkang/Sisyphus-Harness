# Responsibility Decomposition Plan

- Date: 2026-07-22
- Status: Complete
- Scope: remaining oversized runtime classes and modules
- Related debt: `SH-ARCH-001`, `SH-ARCH-002`, `SH-IO-001`, `SH-TEST-001`

## 1. Objective

Reduce responsibility concentration without changing the installed CLI, public
imports, wire contracts, artifact layouts, security invariants, or observed
Agent and Verifier behavior. Each decomposition is delivered independently so
that a regression can be attributed and reverted without coupling unrelated
boundaries.

This work is structural. It does not add authority, relax validation, introduce
new transport claims, or close functional debt such as `SH-ORACLE-001` and
`SH-EVOLVE-001`.

## 2. Verified Baseline

The 2026-07-22 `main` revision contains these responsibility concentrations:

| Target | Size | Concentrated responsibilities | Classification |
| --- | ---: | --- | --- |
| `DockerVerifierTransport` | 952 class lines, 18 methods | image admission, Docker command construction, process capture, host verification orchestration, bundle staging, evidence publication | confirmed oversized class |
| `LocalCodingAgent` | 738 class lines; `run()` is 569 lines | model loop, tool dispatch, compaction, stagnation, verification transitions, artifact recording | confirmed oversized class |
| `cli.py` | 731 module lines; `_main()` is 381 lines | parsing, loading, composition, handlers, rendering, exit policy | confirmed oversized module |
| `SQLiteKnowledgeIndex` | 515 class lines, 18 methods | schema, transactions, projection writes, graph validation, retrieval, revision identity | decomposition candidate |
| `KnowledgeGraph` | 502 class lines, 14 methods | mutation admission, traversal, ranking, dependency inspection, next-step projection | decomposition candidate |
| `WorkspaceTools` | 441 class lines, 16 methods | dispatch, argument parsing, path policy, reads, search, mutation, atomic IO | decomposition candidate |

Large wire-contract modules are excluded from this plan unless a later review
shows mixed authority. Repetitive strict parsing alone is not a god-class
finding.

## 3. Delivery Order

### PR 1: Docker verifier runtime separation

Extract three internal collaborators while retaining
`DockerVerifierTransport` as the public `VerificationServicePort` adapter:

1. a Docker runtime owns immutable image inspection, sandbox command creation,
   process capture, timeout/output enforcement, executable probing, and
   container cleanup;
2. a host verification orchestrator owns exact-view checks, command-result and
   receipt construction, and final binding validation;
3. an evidence publisher owns collision locking, request-first atomic
   publication, rollback, fsync, and authoritative re-read.

The facade owns configuration validation and coordinates these collaborators.
Candidate containers continue to receive only `/workspace` and optional
`/verifier-assets`; candidate output remains non-authoritative.

### PR 2: Agent loop separation

Keep `LocalCodingAgent` as the public application service and extract:

1. deterministic prompt/context rendering;
2. mutable run-state and cadence/compaction transitions;
3. tool-decision and verification-transition handlers;
4. artifact projection and final-result writing.

The model/tool/verifier authority boundary, exact criterion coverage,
monotonic global deadline, state-cycle behavior, hashes, and artifact JSON must
remain byte-compatible unless a canonical timestamp makes byte equality
impossible; in that case parsed equality is required.

### PR 3: CLI separation

Move parser construction, command handlers, rendering, and runtime composition
behind `interfaces/cli/` modules. Keep `sisyphus_harness.cli:main`, arguments,
stdout/stderr, and exit codes compatible. Add table-driven parity tests for all
subcommands before deleting branches from the compatibility entry point.

### PR 4: Knowledge separation

Split SQLite connection/schema lifecycle from projection persistence and query
execution. Split graph traversal/ranking from dependency and next-step domain
services only where the extracted component has a narrow input/output contract.
Deterministic ordering, cycle rejection, revision digests, and non-authoritative
projection semantics are mandatory invariants.

### PR 5: Workspace tool separation

Keep `WorkspaceTools.execute()` as the Agent-facing facade. Extract command
argument decoders and read/search/mutation handlers, then route all file access
through the shared bounded IO primitive planned by `SH-IO-001`. Preserve
allowlist precedence, protected-path and symlink rejection, optimistic hashes,
atomic replacement, output limits, and error text relied upon by the protocol.

## 4. Cross-PR Rules

Every PR must satisfy all of the following before merge:

1. begin from fetched and fast-forwarded `main` on a `codex/*` branch;
2. change one responsibility concentration only;
3. preserve public imports or retain a documented compatibility facade;
4. add architecture tests that prevent the extracted responsibility from
   moving back into the facade;
5. pass focused behavior and adversarial tests, then the complete suite with at
   least 90.0% branch coverage;
6. pass Ruff, Bandit medium/high, compileall, lock, documentation, Compose,
   evidence-manifest, source/wheel build, isolated offline install, and
   `git diff --check` gates applicable to the changed boundary;
7. record a dated review with remaining risks and exact evidence;
8. commit, push, open a ready PR, wait for current-head CI, merge only after all
   required jobs pass, then fetch and fast-forward local `main` before the next
   branch.

## 5. Behavioral Equivalence Strategy

Structural tests are not sufficient. Each PR uses characterization tests at the
existing facade:

- equivalent input contracts produce equal parsed outputs and artifact trees;
- the same invalid inputs raise the same public exception type and stable error
  category;
- timeout, output, mutation, collision, race, and symlink cases remain
  fail-closed;
- public imports and entry points remain loadable from an installed wheel;
- in-process and Docker/Compose paths retain their documented conformance
  status and do not inherit stronger claims merely from module movement.

Private helper patch points may move to the new owning component. Such test
changes are accepted only when facade-level characterization still proves the
same behavior.

## 6. Rollback

Each PR is independently revertible because wire schemas and artifact layouts
do not migrate. A failed extraction is reverted as one merge commit before the
next branch begins. No later PR may depend on an unmerged predecessor except by
starting from its refreshed `main` merge revision.

## 7. Completion

The plan is complete when all five PRs are merged, the dated final review finds
no remaining confirmed god class in these surfaces, and the implementation debt
register either closes or precisely narrows `SH-ARCH-001`, `SH-ARCH-002`,
`SH-IO-001`, and `SH-TEST-001` against executable evidence.

## 8. Delivery Progress

| Slice | Status | Local evidence |
| --- | --- | --- |
| PR 1: Docker verifier runtime separation | Merged by PR #13 at `a3a0121` | implementation `f7cb081`; CI run `29915544947` passed all five jobs; facade reduced from 952 to 311 class lines; 70 focused tests and 442 full-suite tests pass; branch coverage 90.3% |
| PR 2: Agent loop separation | Merged by PR #15 at `59f178e` | implementation `d0d0c86`; CI run `29917555768` passed all five jobs; facade reduced from 738 to 119 class lines; 444 tests pass at 90.5% branch coverage |
| PR 3: CLI separation | Merged by PR #17 at `8601a83` | implementation `7f17a45`; CI run `29919408040` passed all five jobs; 43-line facade and five-line `_main()`; 25 command routes covered; 449 tests pass at 90.4% branch coverage |
| PR 4: Knowledge separation | Merged by PR #19 at `ea9d556` | implementation `40e90a4`; CI run `29921307245` passed all five jobs; graph and SQLite facades are 85 and 64 lines; 452 tests pass at 90.5% branch coverage with byte-identical canonical output |
| PR 5: Workspace tool separation | Merged by PR #21 at `06cce47` | implementation `0388201`; CI run `29923115539` passed all five jobs; 90-line facade and six collaborators at or below 193 lines; 456 tests pass at 90.6% branch coverage with byte-identical canonical output |

The [final responsibility review](../reviews/2026-07-22/responsibility-decomposition-final.md)
finds no remaining confirmed god class in the five targeted surfaces. The
broader transport, IO, test, evolution, evidence, and authority debts remain
open under their existing executable exit conditions.
