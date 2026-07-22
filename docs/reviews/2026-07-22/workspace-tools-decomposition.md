# Workspace Tool Responsibility Decomposition Review

- Review date: 2026-07-22
- Base revision: `main@5216b61`
- Implementation revision: `03882014021d289b970cc56a87ae5b9ee539d93d`
- Merge revision: `06cce47ef237e736f88639798d999f8933f9856d`
- Review scope: `WorkspaceTools` argument, path, IO, query, and mutation split
- Delivery status: merged by PR #21 after CI run `29923115539` passed all five jobs

## Findings

No unresolved High finding or observed facade-level behavioral regression is
present in the reviewed change.

| Severity | Status | Finding | Disposition |
| --- | --- | --- | --- |
| Medium | Open IO debt | Bounded reads still use ordinary `Path.open()` after path resolution, and mutation policy checks and final replacement are not one descriptor-relative no-follow operation. | Keep `SH-IO-001` `AMBER`. This structural change isolates the local primitive but does not claim repository-wide race resistance. |
| Low | Resolved | The original 441-line class combined protocol dispatch, parsing, Git inventory, path security, read/search projection, mutation rules, and durability. | The 90-line facade now owns composition, deadline, dispatch, and public error normalization only. Six narrow modules own the operational responsibilities. |
| Low | Resolved | Moving Git inventory could change timeout and error normalization. | Query inventory still uses the facade-provided bounded timeout; unchanged facade tests prove stderr detail, timeout conversion, deterministic ordering, and skipped-file behavior. |
| Low | Accepted design | `workspace_tool_contracts.py` is intentionally small. | It prevents circular imports while preserving one public error/outcome identity re-exported from `sisyphus_harness.tools`; an identity test guards against duplicate boundary types. |
| Low | Accepted test change | The Git-failure mock path moved from the facade to the query owner. | Only the private patch target changed. Calls and assertions remain facade-level, and the parent plan explicitly allows private patch ownership to move. |

## Responsibility Result

| Component | Module lines | Owned responsibility |
| --- | ---: | --- |
| `WorkspaceTools` facade | 90 | composition, six-name dispatch, deadline, public error normalization |
| argument decoder | 91 | strict scalar/list decoding and scope normalization |
| public tool contracts | 16 | one error identity and one wire outcome |
| bounded workspace IO | 102 | UTF-8/size/hash/truncation, atomic replace, fsync |
| mutation handlers | 129 | write, replace, delete use cases |
| path policy | 138 | containment, protection, allowlist, ignore, symlink policy |
| query handlers | 193 | Git inventory, list, read, literal search |

No extracted module exceeds 220 lines. The facade has four methods, no
operational loop, no Git invocation, and no hashing, temporary-file, mode, or
directory-fsync dependency.

## Behavioral Equivalence

All original tool behavior tests remain facade-level. The only edit to them is
the private `subprocess.run` patch target now owned by
`workspace_tool_queries.py`. They continue to cover protected and allowed
roots, lexical/resolved/symlink escapes, ignore controls, stale hashes, no-op
mutations, binary and size limits, exact replacement count, atomic mode
preservation, deadline expiry, output limits, and normalized failures.

Local evidence so far:

- the 45-test pre-change focused baseline and 48-test post-split focused suite
  pass;
- all 456 repository tests pass with three explicit Docker integration skips;
- total branch coverage is 90.6%; the facade and contract module are 100%
  covered and every operational collaborator is at least 92.8% covered;
- the same six-command success/failure sequence on base and current code
  produced byte-identical canonical output, final file bytes, existence, and
  mode with SHA-256
  `83bc08af2c91359980fdf53f40a8a9e97ff5aa4880f6abc07358bcffdac786a9`;
- all 114 non-empty strings in the original module remain in the facade and
  extracted implementation;
- public constructor/`execute()` parameter order, defaults, keyword-only
  behavior, and `ToolError`/`ToolOutcome` re-export identity are guarded;
- Ruff and Bandit medium/high pass;
- lock, compileall, documentation links, Compose parsing, the 126-file
  historical manifest, GEPA, and `git diff --check` pass;
- source and wheel build offline; a clean Python 3.14 install imports every
  extracted module, preserves public identities, performs a real
  write/read/delete sequence, and starts the installed 25-command CLI;
- a source-rebuilt verifier image passes all three real-container boundary
  probes.

PR-head CI run `29923115539` passed `static-and-container`, core on Python 3.11
and 3.14, evolution, and package-and-evidence against implementation head
`03882014021d289b970cc56a87ae5b9ee539d93d`. PR #21 then squash-merged as
`06cce47ef237e736f88639798d999f8933f9856d`, and local `main` was fetched and
fast-forwarded to that exact revision before this delivery record.

## Residual Risk

The optimistic SHA-256 contract detects stale content before mutation but does
not by itself close a path-swap race. The split makes that IO boundary explicit
for a future `SH-IO-001` implementation; it must be upgraded with descriptor-
relative containment and stable-stat tests as a separate security change.
`ToolOutcome.mutated` also remains a claim checked by Agent workspace snapshots,
not task or verification authority.
