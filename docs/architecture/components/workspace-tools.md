# Workspace Tool Boundary

## Responsibility

Workspace tools translate one strictly decoded model decision into a bounded,
repository-local read or mutation. They expose only six file operations and
return observations; they do not decide whether a task or verification passed.

## Owned Authority

- exact dispatch of `list_files`, `read_file`, `search_text`, `write_file`,
  `replace_text`, and `delete_file`;
- lexical and resolved workspace containment for requested paths;
- protected-state, positive write-allowlist, Git-ignore, and symlink policy;
- bounded UTF-8 reads, literal search, output truncation, and skipped-file
  reporting;
- optimistic content-hash checks and mode-preserving atomic file replacement;
- truthful `ToolOutcome.mutated` reporting to the Agent transition layer.

## Forbidden Authority

Workspace tools must not own task admission, queue transitions, attempt
completion, verification commands, verifier assets, evidence publication,
semantic `TaskOutcome`, policy approval or activation, evolution promotion, or
signing keys. A successful mutation is not verification evidence.

## Current Implementation

`src/sisyphus_harness/tools.py` is the public compatibility facade. It composes
the collaborators, owns the monotonic deadline boundary, dispatches the six
stable names, and converts deadline, subprocess, filesystem, and Unicode
failures into `ToolError`.

| Module | Responsibility |
| --- | --- |
| `workspace_tool_contracts.py` | public `ToolError` and `ToolOutcome` identities re-exported by the facade |
| `workspace_tool_arguments.py` | unknown-field rejection, strings, line arrays, scope aliases, and positive integers |
| `workspace_tool_paths.py` | containment, protected roots, allowlist, ignore controls, and write-path symlink policy |
| `workspace_tool_io.py` | bounded UTF-8, SHA-256, truncation, mode-preserving atomic replace, and directory fsync |
| `workspace_tool_queries.py` | Git inventory plus deterministic list, read, and literal search handlers |
| `workspace_tool_mutations.py` | expected-hash write, single replace, and durable delete handlers |

The Agent calls only `WorkspaceTools.execute()`. Query and mutation handlers
share the same path-policy and bounded-IO instances for one run, so configured
limits and write roots cannot diverge between commands.

## Contracts

| Direction | Contract | Meaning |
| --- | --- | --- |
| Inbound | tool name + strict argument object | one allowlisted file operation |
| Inbound policy | workspace, byte/output limits, protected and allowed roots, deadline | run-scoped capability boundary |
| Outbound | `ToolOutcome` | bounded observation and mutation claim |
| Failure | `ToolError` | stable fail-closed protocol error |
| Mutation guard | `sha256:<hex>` | optimistic exact-content precondition |

## Target Boundary

Workspace tools remain an in-process Agent capability unless a later Agent
transport isolates them. A future repository-wide bounded file primitive must
add no-follow opening, stable pre/post stat checks, and race tests without
moving write policy or task authority into generic infrastructure.

## Open Debt And Evidence

- `SH-IO-001`: current bounded reads and atomic replacement do not yet provide
  one repository-wide no-follow, stable-stat API across all call sites.
- `SH-ARCH-002`: Agent and its tool capability do not yet have authenticated
  process/Compose transport parity.
- `SH-TEST-001`: broader suites still require responsibility-based splitting;
  the tool facade itself has focused behavior and structure coverage.

Primary regression suites are `tests/test_tools.py`,
`tests/test_workspace_tool_structure.py`, Agent transition tests, and the
facade guard in `tests/test_architecture_dependencies.py`.
