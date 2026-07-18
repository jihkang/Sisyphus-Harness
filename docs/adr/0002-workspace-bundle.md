# ADR 0002: Immutable Workspace Bundle Between Services

- Status: Accepted
- Date: 2026-07-18

## Context

The current agent and verifier operate on the same host path. A shared writable
path across containers weakens isolation, makes receipt provenance host-dependent,
and allows a verifier or reclaimed worker to affect another execution.

## Decision

Cross-service requests identify workspaces by an immutable artifact reference and
digest rather than an absolute host path. A workspace bundle binds:

- repository and base commit identity;
- staged, unstaged, and untracked content needed to reconstruct the state;
- changed paths;
- workspace state hash;
- bundle content digest and schema version.

Agent creates a new bundle for each verification request. Verifier reconstructs it
inside an ephemeral workspace, confirms the digest and state hash, runs commands,
and destroys the workspace after writing its receipt. A failed receipt can be fed
back to the still-running Agent, which may produce a new bundle for the next repair.

The initial adapter may use a filesystem artifact store, but its contract uses
artifact IDs so an object store can replace it later.

## Consequences

- Verifier never receives Agent's writable workspace volume.
- Bundle extraction must reject absolute paths, parent traversal, device entries,
  and escaping symlinks.
- Large repositories incur packaging and reconstruction cost; caching by base
  commit is an allowed optimization only when digest verification remains intact.
- `AgentResult` and `VerificationReceipt` migrate from host paths to artifact refs
  without changing their existing v1 compatibility serialization immediately.
