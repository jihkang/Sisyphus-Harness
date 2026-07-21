# Implementation Debt Register

- Register date: 2026-07-21
- Scope: Harness architecture through locally validated Slice C command isolation
- Status vocabulary: [canonical conformance model](conformance-model.md)
- Detailed ordering: [remediation roadmap](../reviews/2026-07-21/remediation-roadmap.md)

This is the canonical list of known implementation debt. Priority describes
risk and dependency order; conformance status describes evidence, not urgency.
An item is closed only when its exit condition is executable and passes on the
revision making the claim.

## Authority And Verification

| ID | Priority | Status | Current fact | Exit condition | Slice |
| --- | --- | --- | --- | --- | --- |
| `SH-VERIFY-001` | P0 | `AMBER` Partial | Asset CAS, immutable image execution identity, v2 profile/request/result/outcome, v3 receipt, full binding validation, adversarial tests, and real-Docker tests are implemented locally; current-head CI and merge evidence are pending. | Current-head CI passes the asset/image substitution suite and real Docker probes, the change is merged, and the closure log records the merge revision. | C |
| `SH-VERIFY-002` | P0 | `AMBER` Partial | The default Docker transport now runs every command as direct container PID 1 without request, bundle CAS, or evidence mounts; the host alone captures output and constructs the receipt. Unit and three real-Docker adversarial paths pass locally; current-head CI and merge evidence are pending. | Current-head CI rebuilds the image and proves absent authority mounts, detached-child teardown, mutation failure, host-created evidence, and Control adjudication; the merged revision is recorded in the closure log. | C hardening |
| `SH-ORACLE-001` | P1 | `GRAY` Not evaluated | The current deployment protects verifier asset integrity but deliberately exposes mounted bytes to code in the same container namespace; confidential hidden-oracle execution is neither implemented nor claimed. | If confidentiality becomes a product requirement, a separate process or VM evaluator receives no candidate-readable oracle bytes and returns only a bounded authenticated result; read-attempt tests fail closed. | Deployment profile |
| `SH-GRAPH-001` | P0 | `RED` Non-conformant | `TaskOutcome` binds queue job/attempt lineage, but no admitted Claim, Gap, TaskBasis, TaskGraph dispatch, or source-grounding digest exists. | Control admits a revision-bound task basis and dispatch; stale or mismatched graph/source/contract bindings cannot enqueue or close a task. | Task-graph phases 2-5 |
| `SH-CTRL-001` | P1 | `AMBER` Partial | Outcome rows and artifacts prevent duplicate authority, but concurrent first adjudications have no dedicated Control lease and one caller may need to retry. | A fenced adjudication claim provides bounded retry/idempotency tests without allowing two verifier publications or stale completion. | E |
| `SH-TRUST-001` | P2 | `GRAY` Not evaluated | Authority is local SQLite and filesystem state under one OS account; no external ledger, KMS identity, revocation, or replication is claimed for supervised local use. | A separately scoped deployment profile defines authenticated identity, signing, revocation, retention, replication, and recovery tests. | Post-F / deployment |

## Runtime And Module Boundaries

| ID | Priority | Status | Current fact | Exit condition | Slice |
| --- | --- | --- | --- | --- | --- |
| `SH-ARCH-001` | P1 | `AMBER` Partial | `cli.py` still combines parsing, command handlers, path loading, composition, rendering, and exit policy. | Parser, handlers, renderers, and composition dependencies are split without changing CLI wire output or exit behavior; parity tests cover every command. | D |
| `SH-ARCH-002` | P1 | `AMBER` Partial | Logical Agent, Verifier, Evolve, and Control boundaries exist, but several run in one package/process and do not have authenticated transport or Compose parity. The 1,252-line Docker adapter also combines CAS isolation, runtime mechanics, host evidence assembly, and publication. | Each service owns only its contracts/artifacts, communicates through explicit ports/transports, passes in-process versus Compose contract tests, and separates Docker process mechanics from host verification orchestration without weakening one publication boundary. | D |
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
