# CLI Interface Boundary

## Responsibility

The CLI converts operator arguments and bounded JSON input into one explicit
use-case invocation, then renders one deterministic JSON result and exit code.
It is an interface boundary, not a source of task, verification, queue, or
policy authority.

## Owned Authority

- public `sisyphus-harness` entry point and argparse contract;
- mapping of each command name to one handler group;
- repository-root resolution and bounded operator input decoding;
- stdout/stderr JSON representation and process exit policy;
- interface-level composition of existing services.

## Forbidden Authority

The CLI must not decide task success, invent verifier evidence, mutate queue
state outside `JobQueue`, approve or activate a policy without `PolicyRegistry`,
or duplicate Agent, Worker, Control, benchmark, evolution, and knowledge rules.
Handlers must not print or hide domain errors.

## Current Implementation

`src/sisyphus_harness/cli.py` is a compatibility facade. It retains `main`,
`_main`, and `build_parser`, owns the public exception boundary, and performs
only parse, dispatch, render, and exit.

| Module | Responsibility |
| --- | --- |
| `interfaces/cli/parser.py` | all 25 command parsers and argparse defaults |
| `interfaces/cli/dispatcher.py` | explicit command-to-handler routing and repository-root resolution |
| `interfaces/cli/result.py` | handler result payload and exit-code contract |
| `interfaces/cli/renderers.py` | deterministic stdout/stderr JSON |
| `interfaces/cli/io.py` | contained paths, strict/bounded JSON, and input digests |
| `interfaces/cli/policy_selection.py` | configured versus active policy selection and evolution-result containment |
| `interfaces/cli/handlers/` | setup, queue, task, execution, policy, and knowledge use-case composition |

Handlers return `CliResult`; only the facade renders. The dispatcher uses an
explicit route table rather than dynamic discovery so every public command is
visible to structural tests and review.

## Contracts

| Direction | Contract | Meaning |
| --- | --- | --- |
| Inbound | argv and bounded JSON | operator-provided command and data |
| Dispatch | `argparse.Namespace` | parser-normalized command values |
| Handler result | `CliResult` | one JSON-serializable payload and exit code |
| Error result | stderr JSON | stable `error` and `error_type`, exit code 2 |
| Domain result | service contracts | existing Agent, Worker, Control, policy, benchmark, evolution, and graph behavior |

## Target Boundary

New commands should add parser declarations and one explicit handler route,
while business rules remain in application/domain services. Interface handlers
may eventually receive composition ports where concrete construction becomes a
test or deployment bottleneck; a general dependency-injection container is not
required by this boundary.

## Open Debt And Evidence

- `SH-COMPAT-001`: the compatibility facade has no explicit pre-1.0 sunset
  decision.
- `SH-IO-001`: CLI file reads do not yet all use one race-resistant bounded IO
  primitive.
- `SH-TEST-001`: facade characterization remains concentrated in
  `tests/test_cli.py`.

Primary evidence is `tests/test_cli.py`, `tests/test_knowledge_cli.py`,
`tests/test_cli_structure.py`, and the CLI facade guard in
`tests/test_architecture_dependencies.py`.
