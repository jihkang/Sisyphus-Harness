# Knowledge Decision-Support Boundary

## Responsibility

Knowledge stores a rebuildable candidate projection and provides deterministic,
revision-bound search, dependency inspection, and next-step context. Its output
supports planning but never admits, dispatches, completes, or promotes a task.

## Owned Authority

- strict candidate `KnowledgeNode` and `KnowledgeEdge` projection;
- atomic rejection of dependency cycles in the derived SQLite index;
- lexical term scores and bounded deterministic graph traversal;
- dependency and next-step candidate explanations;
- whole-index integrity validation and revision digest.

## Forbidden Authority

Knowledge must not own source/evidence admission, Claim or Gap truth, TaskBasis,
queue transitions, Worker attempts, final verification, `TaskOutcome`, policy
approval, activation, signing keys, or promotion. A high-ranked candidate and an
eligible projection are not execution authorization.

## Current Implementation

`src/sisyphus_harness/knowledge_graph.py` remains the public domain facade and
depends on `KnowledgeIndexPort`. Responsibilities behind it are separated as
follows:

| Module | Responsibility |
| --- | --- |
| `knowledge_mutations.py` | exact node/edge admission and dependency write-result mapping |
| `knowledge_read_context.py` | one revision fence, shared node/edge caches, deterministic traversal |
| `knowledge_search.py` | lexical plus graph scoring and ranked search projection |
| `knowledge_dependencies.py` | dependency state, satisfaction, truncation, and reasons |
| `knowledge_planning.py` | candidate eligibility, ranking, and `NextStepContext` |

`src/sisyphus_harness/infra/knowledge_index.py` remains the public SQLite
adapter facade. It inherits the connection/schema lifecycle so consumer
subclasses can still instrument `connection()`, and delegates to these
collaborators:

| Module | Responsibility |
| --- | --- |
| `infra/knowledge_database.py` | schema, connection, write transaction, stable read transaction |
| `infra/knowledge_projection.py` | node/edge writes and atomic dependency-cycle admission |
| `infra/knowledge_queries.py` | node/edge reads, bounded term query, revision read |
| `infra/knowledge_integrity.py` | strict row/payload parity, metadata/term/edge validation, revision digest |

The SQLite schema and public import paths are unchanged. Every write validates
the existing index and proposed result inside one transaction, while each graph
read checks the start revision again after projection.

## Contracts

| Direction | Contract or port | Meaning |
| --- | --- | --- |
| Inbound mutation | `KnowledgeNode`, `KnowledgeEdge` | candidate-only derived data |
| Storage port | `KnowledgeIndexPort` | rebuildable projection operations |
| Search result | `KnowledgeSearchHit` | lexical and path score with revision binding |
| Dependency result | `DependencyInspection` | candidate state, not readiness authority |
| Planning result | `NextStepContext` | ranked decision-support candidates |
| Integrity | revision SHA-256 | canonical metadata, node, edge, and term identity |

## Target Boundary

Source ingestion may rebuild this projection through a dedicated adapter, but
admitted evidence and TaskGraph authority must remain in Control-owned stores.
If remote deployment is added, the transport must preserve revision binding and
candidate-only authority without turning retrieval results into dispatch
commands.

## Open Debt And Evidence

- `SH-GRAPH-001`: admitted Claim/Gap/TaskBasis/TaskGraph authority is absent.
- `SH-ARCH-002`: service/process transport separation is incomplete.
- `SH-TEST-001`: knowledge boundary tests remain large despite strong behavior
  coverage.

Primary regression suites are `tests/test_knowledge_graph.py`,
`tests/test_knowledge_boundaries.py`, `tests/test_knowledge_cli.py`,
`tests/test_evidence_graph_architecture.py`, and the facade guard in
`tests/test_architecture_dependencies.py`.
