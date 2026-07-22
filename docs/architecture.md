# Architecture

Sisyphus Harness separates authority, execution, verification, and optimization
so a model cannot turn a successful coding action into lifecycle authority.

For the complete module map, storage schemas, direct and queued execution flows,
benchmark scoring, GEPA evolution, and policy data lineage, see
[Architecture and Data Pipeline](architecture-and-data-pipeline.md).
Container service boundaries and ownership decisions are recorded under
[`docs/adr`](adr/).

## Components

`authority.py`, `database.py`, and `queue.py`

Resolve the Git common directory, own SQLite schema and transactions, and
control idempotent jobs, leases, heartbeats, execution-terminal transitions,
immutable `AttemptFinished` lineage, and Control-owned `TaskOutcome` records.

`tools.py`, `workspace_tool_*.py`, and `workspace.py`

Expose six repository-local file tools. `tools.py` is the public facade;
argument decoding, path/write policy, bounded IO, query handlers, and mutation
handlers are separately owned. Paths are contained after resolution, Git,
authority paths, and the configuration loaded for a run are protected from
model writes, lexically ambiguous and Git-ignored write targets are rejected,
existing writes require a content hash, and writes are atomic.
Workspace snapshots bind a commit SHA to staged, unstaged, and untracked
content.

`agent.py`, `agent_loop.py`, `agent_context.py`, `agent_state.py`,
`agent_transitions.py`, `agent_artifacts.py`, `protocol.py`, and `provider.py`

Run the local coding loop. `agent.py` is the public composition facade;
provider/protocol orchestration, deterministic context, mutable run state,
tool and verification transitions, and artifact projection are separately
owned behind it. The provider must return exactly one JSON decision. The
harness controls observation, reflection, compaction, tool execution,
stagnation detection, budgets, and final verification.

`verifier.py`, `services/verifier.py`, and `adapters/docker_*.py`

Executes operator-defined argv without a shell. It records full stdout and
stderr, executable identity, timeout and exit state, and before/after workspace
hashes. A verifier that mutates the workspace cannot produce a passing receipt.
The compatibility bundle service verifies an immutable workspace reference and
an exact content-addressed verifier asset tree. `adapters/docker_verifier.py` is
the public facade; `adapters/docker_bundle_view.py`, `adapters/docker_runtime.py`,
`adapters/docker_host_verification.py`, and `adapters/docker_evidence.py` own exact input staging,
container process mechanics, host evidence assembly, and publication
respectively. The default Docker path resolves and rechecks an immutable image
ID and runs each `CommandSpec` as PID 1 in a separate no-network,
resource-bounded container. Candidate code receives only the workspace
read-write and exact assets read-only; request, bundle CAS, and evidence paths
are absent. The host bounds output, measures workspace and asset state,
constructs the v3 receipt, validates every source/result binding, and atomically
publishes the run. The asset mount protects integrity, not secrecy from
candidate code.

`contracts/control/`, `contracts/evidence_contract.py`, `evidence_contract.py`,
`services/evidence_contract.py`, `services/control_outcomes.py`, and
`infra/control_outcomes.py`

Define immutable evidence selectors, clauses, bounded predicate trees, and
three-valued evaluation. Control independently verifies the exact Worker output
bundle, re-reads the digest-bound receipt artifact, normalizes command facts,
and evaluates the contract without consulting the Agent success Boolean.
`ControlTaskOutcomeService` reloads the current fenced attempt and is the only
application service that can publish the resulting semantic decision.

`contracts/knowledge.py`, `knowledge_graph.py`, `knowledge_mutations.py`,
`knowledge_read_context.py`, `knowledge_search.py`,
`knowledge_dependencies.py`, `knowledge_planning.py`, and
`infra/knowledge_database.py`, `infra/knowledge_index.py`,
`infra/knowledge_projection.py`, `infra/knowledge_queries.py`, and
`infra/knowledge_integrity.py`

Provide a rebuildable candidate-only knowledge graph. Search, dependency
inspection, and next-step projections are deterministic and revision-bound.
The public graph facade delegates mutation, shared cached traversal, search,
dependency, and planning. The public SQLite facade inherits schema/transaction
lifecycle and delegates projection writes, reads, and whole-index integrity.
These components have no Claim, Gap, Task admission, dispatch, or completion
authority.

`benchmarks.py`, `evolution.py`, and `policy.py`

Copy visible fixture workspaces into isolated Git repositories, keep hidden
verifiers external, score rollouts, run GEPA offline, independently reevaluate
candidates, and require a signed operator approval before activation.

`cli.py`, `interfaces/cli/`, `runtime.py`, and `worker.py`

Provide direct commands, trust-mode composition, and leased queue execution.
`cli.py` is a compatibility facade that owns the public entry point and error
boundary. `interfaces/cli/parser.py`, `dispatcher.py`, `handlers/`,
`renderers.py`, `io.py`, and `policy_selection.py` separately own argparse,
explicit command routing, use-case composition, deterministic JSON output,
bounded inbound data, and effective policy selection. Handlers return one
`CliResult` and do not print or absorb domain authority.
The default `untrusted-contained` mode requires an operator-owned write
allowlist and sends immutable candidate bundles through Docker verification;
host verification requires explicit `trusted-in-process`. Workers load one
immutable job payload containing the input workspace bundle and effective
config/policy snapshot, heartbeat the lease, materialize an isolated per-attempt
repository, publish an output bundle, and write exactly one terminal queue
result plus `AttemptFinished` record. Queue completion means execution finished,
not that the task passed. Lease ownership and the attempt number fence the
database transition; an expired attempt
cannot mutate the operator's source repository because its workspace is
disposable authority state.

## Authority Flow

```text
operator config
      |
      v
submit-time config/policy + workspace bundle
      |
      v
isolated Worker attempt -> bounded model decision -> contained file tool
      |                                      |
      +---------------- repeated ------------+
      |
      v
output bundle -> independent verifier -> authoritative receipt re-read
      |
      v
Control EvidenceContract evaluation (Agent success is diagnostic only)
      |
      v
immutable TaskOutcome (passed / failed / indeterminate)
      |
      v
offline benchmark/evolution -> proposed candidate
      |
      v
operator approval -> signed active policy
```

The policy can influence strategy and cadence. It cannot replace safety text,
tool schemas, path checks, budgets, verifier behavior, queue authority, or
activation gates.

## Storage

All authority data is rooted at:

```text
<git-common-dir>/sisyphus-harness/
```

Linked worktrees therefore share queue and policy authority. Agent workspaces
cannot reach this directory through the provided tools. The same root also owns
the rebuildable `knowledge-index.sqlite3`, content-addressed workspace bundles,
disposable attempt workspaces, and Agent/verification/evolution artifacts.
`authority.sqlite3` keeps queue rows, append-only `attempt_finished` lineage, and
append-only `task_outcomes` as separate projections.
