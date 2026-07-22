# Docker Verifier Decomposition Review

- Review date: 2026-07-22
- Base revision: `main@b28ffcbc57506701939d71d53d77cdbd93618c8d`
- Implementation revision: `f7cb081e4819113fa69005de89e9cfae5862258b`
- Merge revision: `a3a0121eb7b245828de9cca4001da7c568f30d85`
- Review scope: Docker verifier responsibility decomposition
- Delivery status: merged by PR #13 after CI run `29915544947` passed all five jobs

## Findings

No high-severity behavioral or authority regression was found in the reviewed
change.

| Severity | Status | Finding | Disposition |
| --- | --- | --- | --- |
| Medium | Open debt | Agent, Verifier, Evolve, and Control do not yet have authenticated process transports or full Compose parity. | `SH-ARCH-002` remains `AMBER`; this change narrows only the Docker adapter concentration. |
| Low | Accepted compatibility | `DockerVerifierTransport` retains private one-line delegates used by existing failure-injection tests. | The AST guard caps the facade at 325 lines and forbids process-capture logic from returning; remove delegates only with an explicit compatibility/test migration. |
| Low | Open test debt | `test_verification_service_edges.py` remains over 1,600 lines. | Keep under `SH-TEST-001`; split by persistence, runtime, and adversarial responsibility in a separate PR. |

## Responsibility Result

| Component | Class lines | Owned responsibility |
| --- | ---: | --- |
| `DockerVerifierTransport` | 311 | public configuration, staging coordination, port facade, compatibility delegates |
| `DockerRuntime` | 269 | image identity, sandbox command, executable provenance, command capture |
| `DockerProcessRunner` | 169 | process lifetime and bounded stdout/stderr collection |
| `DockerHostVerifier` | 233 | host observations, command results, receipt/result binding |
| `DockerEvidencePublisher` | 59 | collision lock, request-first publication, rollback, fsync |
| `prepare_bundle_view()` | 76 | stable-stat exact CAS copy and reference equality |

The previous 952-line class no longer imports selector, signal, stat, hashlib,
or threading mechanics. `tests/test_architecture_dependencies.py` enforces the
facade size, forbidden imports, removed collector methods, and collaborator
presence.

## Behavioral Equivalence

The existing facade-level suites were retained rather than rewritten around
the new implementation. They continue to cover immutable image resolution,
exact bundle and asset views, sandbox flags, direct PID 1 commands, combined
output limits, global/per-command deadlines, CID cleanup, workspace and asset
mutation, host-owned receipt assembly, binding substitution, collision locking,
rollback, stable re-read, and legacy result parsing.

Local evidence on the working revision:

- 70 focused architecture/Docker/edge tests passed;
- 442 complete tests passed with 3 explicit Docker integration skips;
- total branch coverage is 90.3%; extracted modules range from 87.9% to 99.4%,
  with uncovered paths concentrated in platform fallbacks and defensive IO
  exceptions already covered at the facade category level;
- the rebuilt verifier image passed all 3 real-Docker boundary probes;
- Ruff 0.15.22 and Bandit 1.9.4 passed with zero medium/high findings;
- lock validation, `compileall`, historical manifest verification, Compose
  parsing, GEPA integration, and `git diff --check` passed;
- offline source/wheel build, isolated `--no-index --no-deps` installation, and
  installed CLI startup outside the source tree passed.

PR-head CI run `29915544947` passed `static-and-container`, core on Python
3.11 and 3.14, evolution, and package-and-evidence against implementation head
`f7cb081e4819113fa69005de89e9cfae5862258b` before squash merge.

## Residual Risk

This is a structural refactor and does not improve oracle confidentiality.
Verifier assets remain candidate-readable when mounted. It also does not make
standalone Compose equivalent to the host-owned evidence path. Those limits
remain `SH-ORACLE-001` and `SH-ARCH-002` respectively.
