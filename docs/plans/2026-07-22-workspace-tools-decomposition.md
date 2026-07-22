# Workspace Tool Responsibility Decomposition Plan

- Date: 2026-07-22
- Status: In progress
- Base revision: `main@5216b61`
- Parent plan: [responsibility decomposition](2026-07-22-responsibility-decomposition.md)
- Related debt: `SH-ARCH-002`, `SH-IO-001`, `SH-TEST-001`

## 1. Verified Problem

`src/sisyphus_harness/tools.py` is 642 lines and `WorkspaceTools` spans 441
class lines across 16 methods. One object currently owns six-command dispatch,
argument decoding, deadline conversion, Git inventory, repository path policy,
bounded UTF-8 reads, literal search, optimistic mutation checks, atomic replace,
and directory durability.

The existing behavior is security-sensitive and already strongly tested. The
objective is therefore responsibility isolation, not a tool protocol redesign.
The 45-test focused baseline covering tools, architecture, and wire encoding
passes at `main@5216b61` before extraction.

## 2. Required Invariants

1. `sisyphus_harness.tools.ToolError`, `ToolOutcome`, and `WorkspaceTools`
   retain their public identities, constructor and method signatures, and wire
   behavior.
2. `execute()` supports exactly `list_files`, `read_file`, `search_text`,
   `write_file`, `replace_text`, and `delete_file`; unsupported tools and
   unknown fields continue to fail closed with the same messages.
3. Validation order remains stable: arguments precede path access, write policy
   precedes mutation, current content precedes optimistic hash comparison, and
   size/no-op checks precede publication.
4. Lexical containment, resolved containment, `.git` and
   `.sisyphus-harness` protection, `.gitignore` protection, configured protected
   paths, the positive write allowlist, ignored-path rejection, and symlink
   rejection remain mandatory.
5. Reads retain byte, UTF-8, binary, line, result-count, and output-character
   limits. Search remains literal, deterministic, and bounded.
6. Existing-file writes require the exact current SHA-256; creation requires a
   null hash. Replace requires one occurrence. Writes preserve file mode,
   replace atomically, fsync the file and containing directory, and clean up
   temporary files. Delete fsyncs the containing directory.
7. The monotonic global deadline continues to bound Git subprocesses and all
   public filesystem, Unicode, deadline, and subprocess failures retain the
   existing `ToolError` normalization boundary.
8. No extracted component gains Agent, Verifier, Evolve, Control, queue,
   policy, or artifact authority.

## 3. Target Responsibilities

### Compatibility facade: `tools.py`

- re-export the public error and outcome types;
- validate configured policy paths by composing collaborators;
- route the six stable tool names;
- enforce the global deadline and normalize public errors;
- contain no command implementation, Git command, hashing, or filesystem
  mutation mechanics.

### `workspace_tool_arguments.py`

- own strict unknown-field rejection and scalar/list argument decoding;
- preserve newline normalization, text-versus-lines exclusivity, and exact
  validation messages;
- contain no filesystem or subprocess dependency.

### `workspace_tool_paths.py`

- own lexical and resolved repository containment;
- own protected state, protected write roots, positive allowlist, ignore-file,
  ignored-target, and write-path symlink policy;
- create parent directories only after every policy check passes.

### `workspace_tool_io.py`

- own bounded UTF-8 reads, content-size checks, SHA-256, output truncation,
  mode-preserving atomic replace, temporary cleanup, and directory fsync;
- remain a WorkspaceTools-local primitive. It narrows but does not close the
  repository-wide shared-reader exit condition in `SH-IO-001`.

### `workspace_tool_queries.py`

- own Git tracked/untracked inventory and the list, read, and literal-search
  use cases;
- apply path and IO collaborators while retaining deterministic ordering,
  skipped-file reporting, and output limits.

### `workspace_tool_mutations.py`

- own write, single-replacement, and delete use cases;
- enforce expected hashes, target state, no-op rejection, and durable
  publication through the path and IO collaborators.

The extracted modules use direct, narrow composition. No registry framework,
service locator, generic command bus, or filesystem repository abstraction is
introduced.

## 4. Implementation Sequence

1. Move public data/error identities into a small internal contract module and
   re-export them from `tools.py` with identity tests.
2. Extract argument decoding without altering handler validation order.
3. Extract the path-policy and bounded-IO collaborators, retaining every
   adversarial containment, protected-path, ignore, symlink, binary, size,
   hash, atomicity, and fsync assertion.
4. Extract query and mutation handlers and replace `WorkspaceTools` methods
   with facade dispatch to the two collaborators.
5. Add table-driven facade parity tests for all six routes, public signatures,
   error normalization, and the observable mutation/read projections.
6. Add an AST architecture guard that caps the facade, forbids operational
   loops and direct filesystem/Git/hash imports, requires all collaborators,
   and caps each extracted module.
7. Update the architecture map, data-pipeline narrative, debt register, parent
   progress, and dated implementation review.

## 5. Regression Gates

Focused gates cover `tests.test_tools`, Agent transition/context suites,
contract codec/public exports, architecture dependencies, and documentation.
The complete repository gate requires at least 90.0% branch coverage plus
Ruff, Bandit medium/high, compileall, lock validation, documentation links,
Compose parsing, the historical evidence manifest, GEPA, offline source/wheel
build, isolated installed-wheel imports and tool smoke, and all real-container
boundary probes.

Delivery follows the parent protocol: implementation commit, push, ready PR,
all five exact-head CI jobs, squash merge, fetched/fast-forwarded `main`, then a
separate merge-evidence commit and PR with the same current-head CI requirement.

## 6. Behavioral Equivalence Evidence

The implementation will compare base and extracted facades using:

- unchanged original `tests/test_tools.py` behavior assertions except private
  patch ownership paths;
- exact public signature and type-identity projections;
- deterministic success projections for all six commands over equivalent Git
  fixtures, excluding temporary workspace paths;
- exact public exception type/message projections for malformed arguments,
  stale hashes, protected/ignored/symlink paths, deadline expiry, Git failure,
  binary/oversized input, and raw filesystem failure;
- identical final file bytes and modes for successful mutation sequences.

## 7. Non-goals

- adding tools or changing model protocol schemas;
- changing write permissions, search semantics, hash format, or output fields;
- claiming repository-wide race-resistant IO conformance;
- introducing remote transport, container isolation, or new authority;
- splitting unrelated tests or runtime services in this PR.

## 8. Rollback And Completion

No persisted schema or wire contract migrates, so the extraction is one
independently revertible PR. This slice is complete only after local and
current-head CI evidence passes, the implementation and evidence PRs merge,
local `main` is refreshed, and the parent plan records that no targeted
responsibility concentration remains. Residual `SH-IO-001`, `SH-ARCH-002`, and
`SH-TEST-001` work must stay explicitly open unless their broader executable
exit conditions are separately met.
