# CLI Responsibility Decomposition Plan

- Date: 2026-07-22
- Status: Ready for delivery
- Base revision: `main@0fc4823`
- Parent plan: [responsibility decomposition](2026-07-22-responsibility-decomposition.md)
- Related debt: `SH-ARCH-001`, `SH-COMPAT-001`, `SH-TEST-001`

## 1. Verified Problem

`src/sisyphus_harness/cli.py` is 731 lines. `build_parser()` is 152 lines and
`_main()` is a 381-line chain that parses 25 commands, resolves paths, reads
operator input, constructs runtime services, executes commands, renders JSON,
and decides exit status. The public entry point is stable, but every new command
adds imports and branches to the same module and broadens its failure surface.

The objective is responsibility isolation, not a command redesign. Installed
entry points, arguments, defaults, output objects, JSON formatting, error
categories, and exit codes must remain unchanged.

## 2. Required Invariants

1. `sisyphus_harness.cli:main`, `build_parser()`, and `_main()` remain callable
   with the same signatures; `pyproject.toml` does not change.
2. All 25 command names, option names, required flags, defaults, choices,
   repeatable criteria, trusted verification flag, and argparse behavior remain
   unchanged.
3. Successful output remains one `indent=2`, `sort_keys=True` JSON document on
   stdout. Structured errors remain on stderr with `error` and `error_type`,
   and the same exception family maps to exit code 2.
4. Domain outcomes retain their current exit policy: ordinary success is 0;
   failed verification, Agent, benchmark, evolution, worker, or adjudication
   outcomes remain 1 where currently specified.
5. Repository-relative input continues through `contained_path`; strict JSON,
   byte limits, config/policy snapshots, and digest calculation do not weaken.
6. Handler extraction must not move queue, Control, verification, policy, or
   knowledge authority into the interface layer. Handlers only compose and call
   existing application/infrastructure services.
7. Existing facade-level CLI and knowledge CLI tests continue to invoke
   `sisyphus_harness.cli.main`. Private test patch paths may move to the module
   that now owns the concrete dependency.

## 3. Target Responsibilities

### Compatibility facade: `cli.py`

- re-export `build_parser`;
- parse and dispatch through a thin `_main()` delegate;
- retain the exact public exception-to-stderr/exit-2 boundary;
- render the returned payload once and contain no command-specific branch.

### `interfaces/cli/parser.py`

- own all argparse construction and parser-only argument helpers;
- contain no runtime, persistence, provider, or rendering dependency.

### `interfaces/cli/result.py` and `renderers.py`

- represent one command payload and exit code;
- own deterministic stdout/stderr JSON rendering only.

### `interfaces/cli/io.py` and `policy_selection.py`

- own bounded/strict inbound JSON and repository-contained paths;
- own config-versus-active policy selection and contained evolution-result
  lookup shared by task, execution, and policy commands.

### `interfaces/cli/dispatcher.py`

- resolve the repository root once;
- route each command to one responsibility handler group;
- reject an unhandled parsed command without importing concrete domain
  services.

### `interfaces/cli/handlers/`

- `setup.py`: authority initialization, verifier asset creation, profile
  projection;
- `queue.py`: enqueue, claim, heartbeat, finish, and get transitions;
- `task.py`: immutable task submission, status projection, Control
  adjudication, and one Worker lease execution;
- `execution.py`: direct verification, Agent run, benchmark, and evolution
  composition;
- `policy.py`: approval, activation, and active-policy display;
- `knowledge.py`: candidate-only graph initialization, mutation, and queries.

Handlers return `CliResult` and never print. No registry abstraction is added;
the dispatcher uses explicit command groups so routing remains reviewable.

## 4. Implementation Sequence

1. Add the parser module verbatim and prove all command parsing through the old
   facade.
2. Add result/rendering and inbound IO helpers without changing formatting or
   exception normalization.
3. Extract setup and queue handlers, then task/Control, execution/evolution,
   policy, and knowledge handlers one group at a time.
4. Add an explicit dispatcher and replace `_main()` with parse, dispatch,
   render.
5. Move private test patches to the new owning modules while retaining all
   facade calls and assertions.
6. Add table-driven coverage for every parser command and an AST guard that
   caps the facade, forbids command loops/branches and heavy runtime imports,
   and requires the interface collaborators.
7. Update architecture, data-pipeline, debt, parent progress, and a dated code
   review.

## 5. Regression Gates

Focused gates include `tests.test_cli`, `tests.test_knowledge_cli`, runtime,
queue, worker, policy, benchmark, evolution, public-export, architecture, and
documentation suites. Parser coverage enumerates all 25 commands and their
command-specific required arguments/defaults.

The delivery gates are the parent plan gates: 90.0% or greater branch coverage,
Ruff, Bandit medium/high, compileall, lock, documentation links, Compose,
historical manifest, GEPA, offline source/wheel build, isolated installation,
installed CLI startup, current-head CI, squash merge, refreshed `main`, and a
merge-evidence record.

## 6. Non-goals

- changing command names, adding aliases, or adopting a new CLI framework;
- changing runtime trust modes or domain service APIs;
- converting every concrete constructor into a dependency-injection container;
- changing JSON schemas, human-readable output, or exit semantics;
- splitting KnowledgeGraph, SQLiteKnowledgeIndex, or WorkspaceTools in this PR.

## 7. Rollback

The entry point and command contracts do not migrate. The PR remains
independently revertible to the single-module dispatcher, and no persisted
artifact or database schema changes.

## 8. Local Completion Evidence

- all 449 tests pass with three opt-in Docker tests skipped in the ordinary
  suite; total branch coverage is 90.4%;
- all 25 commands have table-driven parser and dispatcher coverage, while the
  existing facade suites retain output and exit-code characterization;
- the compatibility facade is 43 lines and `_main()` is five lines; every CLI
  component is below the 220-line architecture cap;
- original/new parser action projections, public signatures, command strings,
  input ordering, and fail-before-write policy validation were compared;
- Ruff, Bandit medium/high, lock, compileall, documentation links, Compose,
  historical manifest, GEPA, and `git diff --check` pass;
- source and wheel build offline; a clean Python 3.14 environment imports the
  compatibility and dispatcher surfaces and runs the installed 25-command CLI;
- a source-rebuilt verifier image passes all three real-container boundary
  probes.

Current-head PR CI, merge, refreshed `main`, and exact delivery identifiers
remain pending and must be recorded separately.
