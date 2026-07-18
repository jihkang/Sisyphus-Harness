# Architecture

Sisyphus Harness separates authority, execution, verification, and optimization
so a model cannot turn a successful coding action into lifecycle authority.

## Components

`authority.py`, `database.py`, and `queue.py`

Resolve the Git common directory, own SQLite schema and transactions, and
control idempotent jobs, leases, heartbeats, and terminal transitions.

`tools.py` and `workspace.py`

Expose six repository-local file tools. Paths are contained after resolution,
Git, authority paths, and the configuration loaded for a run are protected from
model writes, existing writes require a content hash, and writes are atomic.
Workspace snapshots bind a commit SHA to staged, unstaged, and untracked
content.

`agent.py`, `protocol.py`, and `provider.py`

Run the local coding loop. The provider must return exactly one JSON decision.
The harness controls observation, reflection, compaction, tool execution,
stagnation detection, budgets, and final verification.

`verifier.py`

Executes operator-defined argv without a shell. It records full stdout and
stderr, executable identity, timeout and exit state, and before/after workspace
hashes. A verifier that mutates the workspace cannot produce a passing receipt.

`benchmarks.py`, `evolution.py`, and `policy.py`

Copy visible fixture workspaces into isolated Git repositories, keep hidden
verifiers external, score rollouts, run GEPA offline, independently reevaluate
candidates, and require a signed operator approval before activation.

`cli.py` and `worker.py`

Provide direct commands and leased queue execution. Workers load one immutable
job payload, heartbeat the lease, and write exactly one terminal queue result.
Lease ownership fences the database transition, but repository mutation remains
at-least-once unless the operator supplies isolated worktrees or an external
per-repository lock.

## Authority Flow

```text
operator config
      |
      v
queue lease or direct run
      |
      v
bounded model decision -> contained file tool -> step receipt
      |                                      |
      +---------------- repeated ------------+
      |
      v
operator verifier argv -> immutable receipt -> result
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
cannot reach this directory through the provided tools.
