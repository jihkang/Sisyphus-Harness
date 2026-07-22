# Knowledge Responsibility Decomposition Review

- Review date: 2026-07-22
- Base revision: `main@8f76a74`
- Implementation revision: `40e90a450589d2546a697b9296e0339db5e0e948`
- Merge revision: `ea9d556cf934cb4578b4d1fa057e7a98fdf89a49`
- Review scope: `KnowledgeGraph` and `SQLiteKnowledgeIndex` responsibility split
- Delivery status: merged by PR #19 after CI run `29921307245` passed all five jobs

## Findings

No unresolved High finding or observed facade-level behavioral regression is
present in the reviewed change.

| Severity | Status | Finding | Disposition |
| --- | --- | --- | --- |
| Medium | Open product debt | The index remains manually populated candidate data and has no admitted source span, Claim, Gap, TaskBasis, or dispatch authority. | Keep `SH-GRAPH-001` `RED`; module separation does not upgrade candidate output into Control truth. |
| Low | Resolved | A composition-only SQLite facade would have bypassed consumer subclasses overriding `connection()`. | `SQLiteKnowledgeIndex` inherits `SQLiteKnowledgeDatabase`; all writer/reader collaborators call that same facade instance, and unchanged tracing tests prove dynamic dispatch. |
| Low | Resolved | Independent query services could each reread revision and edges during next-step projection. | One `KnowledgeReadContext` owns the start revision and caches across traversal and all dependency inspections; existing exact call-count tests remain unchanged. |
| Low | Accepted design | Domain and adapter errors now occupy small modules. | They are shared boundary identities required by multiple extracted collaborators and are re-exported from the original public modules; identity tests prevent duplicate exception types. |
| Low | Open test debt | `test_knowledge_boundaries.py` remains 990 lines. | Keep `SH-TEST-001` open and split persistence, integrity, concurrency, and graph read cases separately later. |

## Responsibility Result

| Component | Module lines | Owned responsibility |
| --- | ---: | --- |
| `KnowledgeGraph` facade | 85 | public API and limit-aware collaborator composition |
| mutation service | 46 | exact node/edge admission and dependency result mapping |
| read context | 240 | revision fence, node/edge caches, graph/dependency traversal |
| search service | 90 | lexical/graph score and deterministic ranking |
| dependency service | 101 | task dependency projection and unmet reasons |
| planning service | 151 | eligibility, ranking, and next-step context |
| `SQLiteKnowledgeIndex` facade | 64 | public adapter delegates over inherited lifecycle |
| database lifecycle | 213 | schema, connections, write/read transactions |
| projection writer | 257 | node/edge persistence and atomic dependency admission |
| query reader | 143 | node/edge/term queries and revision read |
| integrity codec | 213 | strict row parity, graph/term validation, revision digest |

The previous 502-line graph class and 515-line SQLite class no longer combine
unrelated read, write, traversal, ranking, schema, and integrity control flow.
No replacement component exceeds 280 module lines, and both public facades are
under 100 lines.

## Behavioral Equivalence

All original knowledge-focused tests remain facade-level and unchanged except
for added public identity and architecture assertions. They retain concurrent
reverse-edge cycle admission, corruption-before-write rollback, trigger
rollback, strict row/payload parity, deterministic path ties, bounded
expansion, revision-change failure, and exact revision/edge cache call counts.

Local evidence so far:

- the 36-test pre-change focused baseline and 41-test post-split focused suite
  both pass;
- all 452 repository tests pass with three explicit Docker integration skips;
- total branch coverage is 90.5%; each new knowledge module is at least 94.4%
  covered and both facades are 100% covered;
- an identical seeded depth-three graph on base and current code produced the
  same canonical 16,234-byte revision/search/dependency/next-step JSON with
  SHA-256 `99380b35d5d83997a7d2b0395d2a654c34b5346bde34078b5e51d4011091d571`;
- all public method arguments/defaults and inherited SQLite context-manager
  signatures match the base; the private `_KnowledgeIndex` annotation alias and
  public docstrings are retained for introspection compatibility;
- every non-empty operational string from the two original modules remains in
  the extracted implementation.
- Ruff passed and Bandit reported no medium/high issue;
- lock, compileall, documentation links, Compose parsing, the 126-file
  historical manifest, GEPA, and `git diff --check` passed;
- offline source/wheel build and a clean Python 3.14 install retained public
  error identities, initialized and read the derived index, imported the new
  collaborators, and started the installed CLI;
- a source-rebuilt verifier image passed all three real-container boundary
  probes.

PR-head CI run `29921307245` passed `static-and-container`, core on Python 3.11
and 3.14, evolution, and package-and-evidence against implementation head
`40e90a450589d2546a697b9296e0339db5e0e948`. PR #19 then squash-merged as
`ea9d556cf934cb4578b4d1fa057e7a98fdf89a49`, and local `main` was fetched and
fast-forwarded to that exact revision before this delivery record.

## Residual Risk

The read revision fence detects concurrent change only after projection; it is
not one SQLite read transaction across all graph calls. This matches the
existing behavior and fails closed, but a future performance/consistency change
must preserve port boundaries and avoid holding a database transaction across
domain ranking. The projection remains local SQLite under one OS identity.
