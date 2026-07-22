# CLI Responsibility Decomposition Review

- Review date: 2026-07-22
- Base revision: `main@0fc4823`
- Implementation revision: `7f17a459ba0c70dc100f2851a106263c6e57b18a`
- Merge revision: `8601a8346798ef1cb204902bc265fd1a3c5ea32f`
- Review scope: public CLI responsibility decomposition
- Delivery status: merged by PR #17 after CI run `29919408040` passed all five jobs

## Findings

No unresolved High finding or observed facade-level behavioral regression is
present in the reviewed change.

| Severity | Status | Finding | Disposition |
| --- | --- | --- | --- |
| Medium | Open debt | Strict JSON files and digest reads still use ordinary `Path.read_bytes()`/`open()` rather than the planned common no-follow, stable-stat primitive. | Keep `SH-IO-001` open; do not hide a security-sensitive IO migration inside this structural PR. |
| Low | Resolved | The old `_main()` combined 25 command branches with rendering and exit decisions. | A 43-line facade now performs parse, dispatch, render, and exception normalization only; an AST guard prevents command branches and heavy imports from returning. |
| Low | Accepted design | `CliResult` and the renderer are deliberately small modules. | They define the only handler-to-process contract and enforce the rule that handlers do not print; no generic command framework or dependency-injection container was added. |
| Low | Open test debt | Existing CLI characterization remains split only between the general and knowledge suites. | Keep `SH-TEST-001` open; the new table-driven suite covers route completeness and parser ownership without weakening facade tests. |

## Responsibility Result

| Component | Module lines | Owned responsibility |
| --- | ---: | --- |
| `cli.py` | 43 | public compatibility, exception boundary, parse/dispatch/render |
| `parser.py` | 182 | all argparse declarations and defaults |
| `dispatcher.py` | 83 | 25 explicit routes and repository-root resolution |
| setup/queue/task handlers | 195 combined | authority setup, queue transitions, task and Control composition |
| execution/policy handlers | 190 combined | verify, Agent, benchmark, evolution, approval and activation composition |
| knowledge handler | 130 | candidate-only graph command composition |
| IO/policy/result/rendering helpers | 96 combined | bounded input, policy selection, result and JSON representation |

The prior 731-line module and 381-line `_main()` no longer own parser creation,
command-specific dependencies, path/JSON helpers, or output decisions in one
control-flow chain. Every extracted module stays below the architecture guard's
220-line cap.

## Behavioral Equivalence

The public `sisyphus_harness.cli:main`, `_main`, and `build_parser` signatures
remain unchanged, as does the installed script entry point. Existing facade
tests continue to assert stdout/stderr payloads and exit codes. The new route
suite supplies a complete valid namespace for every public command and proves
that the route table and parser expose the same 25 command names.

Local validation evidence:

- all 449 tests passed with three explicit Docker integration skips and total
  branch coverage of 90.4%;
- focused CLI, knowledge, parser, dependency, and documentation suites passed
  36 tests, and the fail-before-write policy regression passed separately;
- parser action projection and AST comparison with `main@0fc4823` confirmed all
  25 commands, public function signatures, and non-empty operational strings;
- the facade is 43 lines, `_main()` is five lines, and all extracted modules are
  below the enforced 220-line cap;
- Ruff passed and Bandit reported no medium/high issue;
- lock, compileall, documentation links, Compose parsing, the 126-file
  historical manifest, GEPA, and `git diff --check` passed;
- offline source/wheel build and a clean Python 3.14 wheel install retained the
  compatibility imports and installed 25-command CLI;
- a source-rebuilt verifier image passed all three real-container boundary
  probes.

PR-head CI run `29919408040` passed `static-and-container`, core on Python 3.11
and 3.14, evolution, and package-and-evidence against implementation head
`7f17a459ba0c70dc100f2851a106263c6e57b18a`. PR #17 then squash-merged as
`8601a8346798ef1cb204902bc265fd1a3c5ea32f`, and local `main` was fetched and
fast-forwarded to that exact revision before this delivery record.

## Residual Risk

This change separates interface responsibilities only. It does not make the
CLI a security boundary, change Control authority, migrate persisted data,
introduce authenticated service transports, or close shared IO and test-suite
debt.
