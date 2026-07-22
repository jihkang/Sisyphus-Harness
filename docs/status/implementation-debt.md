# Implementation Debt Register

- Register date: 2026-07-22
- Scope: Harness architecture through merged Slice C command isolation
- Status vocabulary: [canonical conformance model](conformance-model.md)
- Detailed ordering: [remediation roadmap](../reviews/2026-07-21/remediation-roadmap.md)

This is the canonical list of known implementation debt. Priority describes
risk and dependency order; conformance status describes evidence, not urgency.
An item is closed only when its exit condition is executable and passes on the
revision making the claim.

## Authority And Verification

| ID | Priority | Status | Current fact | Exit condition | Slice |
| --- | --- | --- | --- | --- | --- |
| `SH-ORACLE-001` | P1 | `GRAY` Not evaluated | The current deployment protects verifier asset integrity but deliberately exposes mounted bytes to code in the same container namespace; confidential hidden-oracle execution is neither implemented nor claimed. | If confidentiality becomes a product requirement, a separate process or VM evaluator receives no candidate-readable oracle bytes and returns only a bounded authenticated result; read-attempt tests fail closed. | Deployment profile |
| `SH-GRAPH-001` | P0 | `RED` Non-conformant | `TaskOutcome` binds queue job/attempt lineage, but no admitted Claim, Gap, TaskBasis, TaskGraph dispatch, or source-grounding digest exists. | Control admits a revision-bound task basis and dispatch; stale or mismatched graph/source/contract bindings cannot enqueue or close a task. | Task-graph phases 2-5 |
| `SH-CTRL-001` | P1 | `AMBER` Partial | Outcome rows and artifacts prevent duplicate authority, but concurrent first adjudications have no dedicated Control lease and one caller may need to retry. | A fenced adjudication claim provides bounded retry/idempotency tests without allowing two verifier publications or stale completion. | E |
| `SH-TRUST-001` | P2 | `GRAY` Not evaluated | Authority is local SQLite and filesystem state under one OS account; no external ledger, KMS identity, revocation, or replication is claimed for supervised local use. | A separately scoped deployment profile defines authenticated identity, signing, revocation, retention, replication, and recovery tests. | Post-F / deployment |

## Runtime And Module Boundaries

| ID | Priority | Status | Current fact | Exit condition | Slice |
| --- | --- | --- | --- | --- | --- |
| `SH-ARCH-002` | P1 | `AMBER` Partial | PR #13 at `a3a0121` separated Docker runtime, staging, host assembly, and publication behind a 311-line facade; CI run `29915544947` passed all five jobs. PR #15 at `59f178e` reduced `LocalCodingAgent` from 738 to 119 class lines and separated loop, context, state, transitions, and artifact projection behind an AST guard; CI run `29917555768` passed all five jobs. Agent, Verifier, Evolve, and Control still share one distribution and several process/composition roots; authenticated transport and in-process versus Compose parity are incomplete. | Each service owns only its contracts/artifacts, communicates through explicit ports/transports, and passes in-process versus Compose contract tests without weakening the single host evidence-publication authority. | D |
| `SH-IO-001` | P1 | `AMBER` Partial | Bundle and receipt paths have stable-stat/no-symlink readers, but strict operator JSON reads and other filesystem call sites do not share one race-resistant primitive. | One bounded file API provides containment, no-follow open, stable stat, size/digest checks, strict parsing, atomic replace, directory fsync, and adversarial race/symlink tests. | D-E |
| `SH-COMPAT-001` | P2 | `AMBER` Partial | Legacy `CodingJobResult`, `models.py` contract aliases, and historical symbol re-exports preserve imports but have no explicit removal version or complete consumer inventory. | Every compatibility export is enumerated, architecture-guarded, documented with a sunset policy, and removed or retained as a deliberate public API before 1.0. | D |
| `SH-ARTIFACT-001` | P2 | `AMBER` Partial | Per-object byte limits and atomic publication exist, but global quota, retention, garbage collection, and crash-recovery policy are incomplete. | Quota/retention are configured, active references are protected, interrupted publication is recovered, and deterministic GC tests preserve authoritative evidence. | E |
| `SH-TEST-001` | P2 | `AMBER` Partial | Security coverage is strong and host-runtime cases now have a dedicated suite, but `test_verification_service_edges.py` is still 1,695 lines and several other suites exceed 700 lines, increasing review and failure-localization cost. | Tests are split by contract, transport, persistence, and adversarial responsibility without changing discovery, coverage, fixtures, or behavior. | D-E |

## Evolution, Evidence, And Delivery

| ID | Priority | Status | Current fact | Exit condition | Slice |
| --- | --- | --- | --- | --- | --- |
| `SH-EVOLVE-001` | P1 | `AMBER` Partial | GEPA optimization is implemented behind `EvolutionEngine`, but a Hermes-backed agent-evolving lifecycle is not a separate adapter/service and GEPA still carries more orchestration meaning than optimizer-only responsibility. | Hermes owns bounded evolution cadence and proposal lifecycle, GEPA is an optimizer adapter, and both use Agent and Verifier ports without Control authority. | D-F |
| `SH-BENCH-001` | P1 | `RED` Non-conformant | Existing Qwen 30.5B evidence is historical smoke evidence with small train/holdout samples, not a current-release improvement claim. | Repeated baseline and evolved train/holdout trials report dispersion and predeclared gates; both sets improve measurably on the installed release wheel. | F |
| `SH-EVIDENCE-001` | P1 | `RED` Non-conformant | Current-release model/config/dataset/policy/image/source identity and before/after artifacts are not fixed in one release evidence manifest. | A verified manifest binds all input digests, trial observations, aggregate statistics, approval, active policy, re-verification, wheel, source, and verifier image. | F |
| `SH-TYPE-001` | P1 | `AMBER` Partial | Runtime tests, Ruff, and Bandit are gated, but strict static typing, property/race/crash suites, and a wider platform matrix are not release gates. | A zero-new-debt type baseline is reduced to zero; supported platforms run deterministic property, concurrency, timeout, and crash-recovery tests in CI. | E |
| `SH-GOV-001` | P0 | `RED` Non-conformant | CI exists, but required human review, CODEOWNERS enforcement, current-head branch protection, and a security-sensitive ownership gate are not proven active. | Repository settings reject stale/unreviewed authority changes and require current checks plus designated review on protected paths. | F / repository settings |
| `SH-SUPPLY-001` | P2 | `AMBER` Partial | Each verifier request now resolves, binds, rechecks, and executes an immutable local image ID; release tags, published image digest pinning, SBOM/provenance, and dependency update policy remain incomplete. | A tagged release publishes wheel/source/image hashes, SBOM and provenance, pins runtime image identity, and reproduces an offline install/build from the release inputs. | F |

## Closure Log

| ID | Closed by | Evidence |
| --- | --- | --- |
| `SH-P0-001` | PR #8 | default-deny write allowlist and mutation regression |
| `SH-P0-002` | PR #9 at `8cccfef` | Worker/Control authority separation and five passing current-head CI jobs |
| `SH-P0-006` | PR #8 | Docker-default verification and real-container boundary probes |
| `SH-VERIFY-001` | PR #11 at `5d872bc` | asset/image substitution and full v2/v3 binding chain passed all five jobs in [CI run 29848008998](https://github.com/jihkang/Sisyphus-Harness/actions/runs/29848008998) |
| `SH-VERIFY-002` | PR #11 at `5d872bc` | host-owned evidence, absent authority mounts, detached-child teardown, mutation failure, and Control publication passed three real-Docker probes in [CI run 29848008998](https://github.com/jihkang/Sisyphus-Harness/actions/runs/29848008998) |
| `SH-ARCH-001` | PR #17 at `8601a83` | parser, explicit dispatch, six handler groups, bounded input, policy selection, result, and rendering are separated behind a 43-line compatibility facade; all 25 commands and original exit/output behavior passed five jobs in [CI run 29919408040](https://github.com/jihkang/Sisyphus-Harness/actions/runs/29919408040) |
