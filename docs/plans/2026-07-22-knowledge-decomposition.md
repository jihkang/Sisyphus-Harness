# Knowledge Responsibility Decomposition Plan

- Date: 2026-07-22
- Status: Complete
- Base revision: `main@8f76a74`
- Parent plan: [responsibility decomposition](2026-07-22-responsibility-decomposition.md)
- Related debt: `SH-ARCH-002`, `SH-TEST-001`

## 1. Verified Problem

`KnowledgeGraph` spans 502 class lines and combines mutation admission,
revision fencing, cached traversal, lexical ranking, dependency inspection, and
next-step projection. `SQLiteKnowledgeIndex` spans 515 class lines and combines
schema lifecycle, connection/transaction policy, projection writes, atomic
dependency-cycle admission, query execution, row validation, and whole-index
integrity hashing.

Both classes are candidate-only and non-authoritative, but their mixed read,
write, and integrity responsibilities make a local change affect concurrency,
deterministic ordering, and corruption detection at once. This PR changes
ownership only; it does not add graph admission, task authority, or ingestion.

## 2. Required Invariants And Obstacles

1. `KnowledgeGraph`, `KnowledgeGraphError`, `SQLiteKnowledgeIndex`,
   `KnowledgeIndexError`, `KnowledgeIndexConflict`, and
   `KNOWLEDGE_INDEX_SCHEMA_VERSION` retain their import paths and identities.
2. `SQLiteKnowledgeIndex` must remain subclassable. Existing overrides of
   `connection()` must observe initialization, writes, queries, and revision
   reads; therefore the facade must inherit the extracted database lifecycle
   rather than hide it behind unrelated composition.
3. Node and edge writes validate the complete existing index before and after
   mutation in one transaction. Any corruption or trigger failure rolls back
   the entire change.
4. Dependency endpoint validation, duplicate/conflict handling, reachability
   cycle detection, and insertion remain in one `BEGIN IMMEDIATE` transaction.
5. Search, dependency inspection, and next-step projection each bind one start
   revision and verify it once at completion. Next-step reuses one node/edge
   cache across candidate traversal and all dependency inspections.
6. Ranking, path tie-breaking, expansion limits, query term normalization,
   truncation semantics, error text, and returned dataclass projections remain
   unchanged.
7. `KnowledgeGraph` depends on `KnowledgeIndexPort`; no domain query module may
   import SQLite, CLI, queue, Worker, Control, policy, provider, or verifier.
8. `SQLiteKnowledgeIndex` remains a rebuildable projection with no execution or
   task authority dependency.

## 3. Target Responsibilities

### Domain facade and services

| Module | Responsibility |
| --- | --- |
| `knowledge_graph.py` | public facade, compatibility constants, collaborator composition |
| `knowledge_mutations.py` | exact model admission and dependency write-result mapping |
| `knowledge_read_context.py` | one revision, node/edge caches, deterministic graph and dependency traversal |
| `knowledge_search.py` | lexical plus graph scoring, ranking, and limit projection |
| `knowledge_dependencies.py` | task dependency state and truncation explanation |
| `knowledge_planning.py` | candidate eligibility, dependency reuse, rank and next-step context |
| `knowledge_graph_errors.py` | shared domain error identity re-exported by the facade |

### SQLite adapter collaborators

| Module | Responsibility |
| --- | --- |
| `infra/knowledge_database.py` | path, schema initialization, connections, write/read transactions |
| `infra/knowledge_integrity.py` | strict row projection, metadata/edge/term validation, cycle check, revision digest |
| `infra/knowledge_projection.py` | node and edge writes plus atomic dependency admission |
| `infra/knowledge_queries.py` | node/edge retrieval, bounded lexical term query, revision read |
| `infra/knowledge_index.py` | public subclassable facade and compatibility exports |
| `infra/knowledge_index_errors.py` | shared adapter error identities |

The facade inherits `SQLiteKnowledgeDatabase` and passes itself to reader and
writer collaborators. This deliberately preserves dynamic dispatch through a
consumer's `connection()` override.

## 4. Implementation Sequence

1. Extract shared error identities, SQLite lifecycle, and strict integrity
   functions without changing the public facade.
2. Extract projection writer and query reader, then reduce
   `SQLiteKnowledgeIndex` to inherited lifecycle plus explicit delegates.
3. Replace the private graph protocol with `KnowledgeIndexPort` and extract the
   mutation service.
4. Introduce one read context that owns revision and caches; move traversal
   there before extracting search and dependency services.
5. Extract next-step planning last so it can reuse the same read context and
   dependency service without extra revision or edge reads.
6. Add AST guards for facade sizes, dependency direction, collaborator use,
   and forbidden SQLite/domain authority imports.
7. Add public identity, subclass dispatch, revision-count, cache-count,
   deterministic parity, corruption rollback, and concurrent cycle regressions.
8. Update architecture, data-pipeline, debt, parent progress, and dated review.

## 5. Behavioral Equivalence Evidence

The pre-change knowledge-focused baseline is 36 passing tests. Existing tests
already cover deterministic search/ranking, path ties, depth limits, dependency
truncation, cache counts, mid-read revision changes, strict row/payload parity,
term-index corruption, transaction rollback, endpoint rules, duplicate writes,
and concurrent reverse-edge cycle admission. These tests stay facade-level.

Additional structure tests must prove the public facade/export identities,
subclass connection dispatch, collaborator boundaries, and that no extracted
domain module acquires execution authority. The complete parent delivery gates
remain mandatory, including 90% branch coverage, offline wheel install,
current-head CI, squash merge, refreshed `main`, and merge-evidence closure.

## 6. Non-goals

- changing contracts or replacing explicit `to_dict()` wire projections;
- implementing source ingestion, Claim/Gap/TaskBasis admission, or task dispatch;
- changing SQLite schema/version, FTS strategy, ranking weights, or limits;
- exposing extracted internal services as new package-level public API;
- splitting the large knowledge test suites in the same structural PR.

## 7. Rollback

No schema, wire contract, or artifact migration occurs. The PR is independently
revertible to the two original classes without data conversion.

## 8. Completion Evidence

- all 452 tests pass with three opt-in Docker tests skipped in the ordinary
  suite; total branch coverage is 90.5%;
- the 41-test knowledge/architecture focused suite passes, retaining unchanged
  concurrency, rollback, revision, cache-count, and deterministic behavior
  tests;
- base/current canonical revision, search, dependency, and next-step output is
  byte-identical at 16,234 bytes and SHA-256
  `99380b35d5d83997a7d2b0395d2a654c34b5346bde34078b5e51d4011091d571`;
- public method arguments/defaults, inherited context-manager signatures,
  exception identities, public docstrings, and operational strings are
  preserved;
- Ruff, Bandit medium/high, lock, compileall, documentation links, Compose,
  historical manifest, GEPA, and `git diff --check` pass;
- source and wheel build offline; a clean Python 3.14 environment initializes
  and reads the installed knowledge index, imports every compatibility surface,
  and starts the installed CLI;
- a source-rebuilt verifier image passes all three real-container boundary
  probes.

Implementation commit `40e90a450589d2546a697b9296e0339db5e0e948`
was delivered by PR #19. CI run `29921307245` passed `static-and-container`,
core on Python 3.11 and 3.14, evolution, and package-and-evidence against that
exact head. The PR squash-merged as
`ea9d556cf934cb4578b4d1fa057e7a98fdf89a49`, after which local `main` was
fetched and fast-forwarded to the same revision.
