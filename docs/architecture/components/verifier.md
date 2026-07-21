# Verifier Boundary

## Responsibility

Verifier executes operator-selected checks against an exact workspace bundle
and emits immutable command observations and a digest-addressed
`VerificationReceipt`. A failed check is evidence; a transport or contract
failure is a service error.

## Owned Authority

- verification command execution and process lifecycle;
- command stdout, stderr, exit, timeout, mutation, and executable provenance;
- verification receipt creation;
- verifier-owned command and fixture asset admission and integrity checking.

## Forbidden Authority

Verifier must not mutate the candidate bundle, publish `TaskOutcome`, interpret
an evidence contract as task success, transition queue jobs, approve policy,
read signing keys, or access the Control database.

## Current Implementation

The default transport in `src/sisyphus_harness/adapters/docker_verifier.py`
validates and materializes an exact workspace bundle on the host. It applies no
network, a read-only root filesystem, dropped capabilities, non-root execution,
resource limits, and bounded output. Each `CommandSpec` is PID 1 in a fresh
container with only `/workspace` read-write and optional `/verifier-assets`
read-only. Request, bundle CAS, evidence staging, authoritative artifacts,
Control state, and signing keys are not mounted.

The host freezes operator files in `FilesystemVerifierAssetBundleStore`, binds
the resulting `VerifierAssetBundleRef` into `VerificationProfile.v2`, resolves
the Docker image tag to an immutable image ID, and binds both through request
v2, result v2, receipt v3, and `TaskOutcome.v2`. The transport checks tag drift,
executes the image ID, mounts only the exact asset tree read-only, and applies
the complete receipt-binding validator before publishing staged evidence. The
host constructs stdout/stderr artifacts, command observations, the v3 receipt,
and the v2 service result; candidate stdout is diagnostic data and is never
parsed as an authoritative service result.

`BundleVerifierService` implements the compatibility in-container
`VerificationExecutorPort`. `DockerVerifierTransport` implements the default
host `VerificationServicePort`, owns runtime identity admission, and does not
invoke that service for Agent or Control verification. Standalone Compose keeps
the shared service/command namespace and carries no host-owned-evidence claim.

## Contracts

| Direction | Contract or port | Meaning |
| --- | --- | --- |
| Inbound | `BundleVerificationRequest.v2` | run, exact workspace, v2 profile, image identity |
| Executor | `VerificationExecutorPort` | execute an already admitted request |
| Service | `VerificationServicePort` | resolve identity and execute through the host boundary |
| Evidence | `VerificationEvidencePort` | authoritative receipt re-read |
| Outbound | `VerificationServiceResult.v2` | request/profile/bundle/image-bound result and receipt reference |
| Observation | `VerificationReceipt.v3` | command facts plus service and asset bindings |

## Oracle Security Model

Read-only mounting protects verifier asset integrity but does not by itself make oracle
bytes confidential from candidate code executing in a command container. Slice C
guarantees that Agent cannot modify or substitute verifier-owned assets and
binds those assets into the evidence chain. A stronger claim that adversarial
candidate code cannot read hidden bytes requires a separate evaluator process or
VM with a deliberately narrow protocol; file modes or another bind mount are not
that boundary.

## Target Boundary

The next verifier boundary is optional confidential-oracle execution. It needs
a separate evaluator process or VM with an external protocol; further file-mode
or mount changes inside the current container cannot meet that requirement.

Operators freeze a reviewed asset tree with `verifier-assets-create`, then build
the digest-bound command profile with `verification-profile-create`. Neither
command grants Agent access to the authority store.

## Open Debt And Evidence

- `SH-VERIFY-001`: implementation and local Docker evidence exist; current-head CI and merge evidence remain.
- `SH-VERIFY-002`: host-owned evidence and command isolation are implemented locally; current-head CI and merge evidence remain.
- `SH-ORACLE-001`: confidential oracle evaluation is not implemented or claimed.
- `SH-IO-001`: strict bounded reads are not yet one shared primitive.
- `SH-SUPPLY-001`: runtime image identity is not release-pinned.

Primary regression suites are `tests/test_verifier.py`,
`tests/test_verifier_service.py`, `tests/test_verification_service_edges.py`,
`tests/test_docker_verifier.py`, `tests/test_docker_verifier_host.py`, and
`tests/test_docker_verifier_integration.py`.
