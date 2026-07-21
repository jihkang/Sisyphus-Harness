# Slice C Verifier Integrity Review

## Scope

This review covers the verifier asset store, profile/request/result/receipt
schema changes, Docker image resolution, Control outcome binding, CLI admission,
and the real-container path introduced by Slice C. A 2026-07-22 follow-up also
covers host-owned evidence and per-command container isolation. It evaluates
verifier-input integrity and evidence ownership. Confidential hidden-oracle
execution is explicitly outside this slice.

## Implemented Boundary

| Concern | Implementation | Evidence |
| --- | --- | --- |
| verifier-owned files | deterministic regular-file CAS with manifest and tree digests | `tests/test_verifier_assets.py` |
| asset admission | `verifier-assets-create` and strict `VerificationProfile.v2` | `tests/test_cli.py` |
| image identity | tag-to-ID resolution, drift check, exact-ID `docker run` | `tests/test_verification_service_edges.py` |
| service chain | request v2, result v2, receipt v3 | contract and evidence-boundary tests |
| outcome chain | `TaskOutcome.v2` snapshots profile and execution identity | `tests/test_control_outcomes.py` |
| runtime mount | exact asset materialization plus read-only Docker mount | Docker unit and integration tests |
| evidence ownership | candidate has no request/CAS/evidence mount; host creates every result artifact | Docker edge and real-container adversarial tests |

The final chain is:

```text
VerifierAssetBundleRef -> VerificationProfile.v2
  -> BundleVerificationRequest.v2 + VerifierExecutionIdentity
  -> VerificationReceipt.v3 + VerificationServiceResult.v2
  -> ContractEvaluation -> TaskOutcome.v2
```

## Findings Resolved Before Commit

| Severity | Finding | Resolution |
| --- | --- | --- |
| High | Docker result parsing checked top-level IDs but could publish a receipt with a mismatched command list or v3 service binding | transport now invokes the full final-binding validator before artifact publication |
| High | a mutable image tag was passed to `docker run` after request construction | host resolves and rechecks the tag, then executes the immutable image ID |
| High | operator scripts and fixtures were outside the request and outcome digest chain | asset CAS reference is bound through profile, request, receipt, and outcome |
| High | the verifier service and candidate commands shared one writable evidence staging namespace, so detached candidate code could race receipt bytes | the default transport now runs each command as direct container PID 1 with no request, CAS, or evidence mount and constructs evidence on the host |
| Medium | the in-container executor exposed an `execution_identity()` method that always raised only to satisfy a structural protocol | split `VerificationExecutorPort` from the host `VerificationServicePort` |
| Medium | documentation treated read-only oracle mounting as if it could imply secrecy | ADR 0007 and architecture docs limit the claim to integrity and substitution resistance |
| Medium | v2 service results could be internally inconsistent until a later Control validation | result construction now requires a v3 receipt with matching bundle, profile, and image identity |
| Medium | a crash after CAS directory publication but before reference publication made an identical retry fail permanently | retry validates the fsynced manifest and file tree, then recreates only the missing reference |

## Adversarial Coverage

The regression suite rejects asset symlinks and special files, source mutation,
stored-object mutation, reference substitution, missing asset authority, image
tag drift, receipt binding mutation, command-shape mutation, output artifact
substitution, Docker start failure, timeout, output overflow, detached children,
and workspace mutation. These failures occur before Docker execution, become a
host-created failed receipt, or stop before staged evidence publication,
depending on the boundary under test.

The real Docker suite proves non-root execution, no network, a read-only root,
bounded capabilities/resources, absent request/CAS/evidence mounts, direct PID 1
command execution, detached-child teardown, exact workspace input, mutation
detection, a read-only verifier asset mount, immutable image identity
propagation, and Control-owned outcome publication.

## Validation State

- focused contract, boundary, persistence, CLI, and runtime suites: passed;
- complete Python suite: 440 tests passed, with three opt-in Docker tests skipped;
- branch coverage: 90.2%, including 100% for the asset reference contract,
  92.0% for the Docker transport, and 89.2% for the asset store;
- opt-in real Docker suite: three tests passed against the locally rebuilt image;
- current-head CI and merge evidence: pending at the time of this review.

## Delivery Follow-up

PR #11 tested head `c0650c7b9d24fde857524107f559833470176d55` with all five
jobs passing in CI run `29848008998`, including the three real-Docker probes, and
merged as `5d872bc6a064e5f5f36aa46df31813a4ca2d4608`. The living debt register therefore
records `SH-VERIFY-001` and `SH-VERIFY-002` as `GREEN`; this dated review retains
the pre-delivery validation statement above for chronology.

## Residual Risk

1. Candidate code can read verifier assets mounted in its container namespace.
   Confidential oracle evaluation remains `SH-ORACLE-001`.
2. Release artifacts do not yet pin and attest one published verifier image;
   per-run local image identity does not close `SH-SUPPLY-001`.
3. Compose is an executor-level compatibility surface and does not reproduce the
   host transport's exact-view creation, image admission, per-command isolation,
   or host-owned evidence logic by itself.
4. Generic bounded/no-follow filesystem operations remain duplicated under
   `SH-IO-001`.
5. The expanded verifier edge suite needs responsibility-based splitting under
   `SH-TEST-001`; this is reviewability debt, not a missing runtime invariant.
