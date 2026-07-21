# Verifier Command Isolation Code Review

- Review date: 2026-07-22
- Base: `main@1d4632c54fde78d195efce1f62ac56c5fbac81fe`
- Reviewed state: working branch before delivery commit
- Disposition: no unresolved High finding; PR CI and merge evidence pending

## Scope

The review covers verifier asset and image identity binding, per-command Docker
execution, executable provenance, timeout/output handling, host-created command
artifacts and receipts, publication ordering, Control consumption, compatibility
surfaces, tests, and current architecture/security claims.

## Findings

| Severity | State | Finding | Resolution or owner |
| --- | --- | --- | --- |
| High | Resolved | Candidate commands and the verifier service shared writable evidence staging, so a detached process could race candidate-written receipt bytes. | Each command is now direct container PID 1 with no request, CAS, or evidence mount. The host alone creates evidence. ADR 0008 owns the invariant. |
| High | Resolved | Candidate stdout was parsed as `VerificationServiceResult`, allowing candidate output to participate in authority. | Default execution treats stdout/stderr only as bounded diagnostics and constructs result/receipt objects from host observations. The parser remains compatibility-only. |
| Medium | Resolved | Timeout and output-limit exceptions discarded already captured diagnostic prefixes. | Bounded prefixes survive container cleanup and are written by the host with a synthetic failure diagnostic. |
| Medium | Resolved | The run directory was committed before its full service-request index, so an index write failure could return an error after evidence became authoritative. | The request record is persisted first; a failed run commit removes that non-authoritative record. Crash recovery remains governed by `SH-ARTIFACT-001`. |
| Medium | Open debt | `adapters/docker_verifier.py` is 1,252 lines and combines stable CAS copying, Docker process mechanics, host evidence assembly, and publication. The responsibilities are cohesive at the security boundary but expensive to review and change. | `SH-ARCH-002`; split runtime mechanics from orchestration only behind an explicit port and preserve one host publication authority. |
| Medium | Open debt | `tests/test_verification_service_edges.py` remains 1,695 lines despite extracting 516 lines of host-runtime cases. | `SH-TEST-001`; continue responsibility-based test decomposition without reducing branch coverage. |
| Low | Accepted | The default path starts one metadata probe and one command container per `CommandSpec`. | Startup cost is accepted for authority isolation. Any batching design must keep candidate code away from evidence and Control state. |

## Behavioral Parity

Wire schemas remain `VerificationProfile.v2`, `BundleVerificationRequest.v2`,
`VerificationServiceResult.v2`, `VerificationReceipt.v3`, and `TaskOutcome.v2`.
Normal command failure still returns a failed receipt. Docker infrastructure or
invalid source/evidence state still raises a transport error and does not return
a successful observation. Legacy v1/v2 decoding and explicit
`BundleVerifierService` execution remain covered, but standalone Compose is
documented as a weaker compatibility topology.

## Proof Executed

- 440 Python tests passed; three Docker-opt-in tests were skipped in the normal
  suite; branch coverage was 90.2%;
- all three real-Docker tests passed against an image rebuilt from the reviewed
  source, including absent authority mounts, detached-child teardown, workspace
  mutation failure, read-only assets, and Control adjudication;
- Ruff, Bandit medium/high, lock validation, compileall, documentation links,
  Compose parsing, historical evidence verification, and `git diff --check`
  passed;
- source and wheel built offline; the wheel installed with
  `--no-index --no-deps` under Python 3.14 and its CLI started outside the source
  tree.

## Residual Security Limits

1. Mounted verifier asset bytes are readable by candidate commands. Confidential
   hidden-oracle execution remains `SH-ORACLE-001`.
2. The standalone Compose executor still shares service and command mounts and
   carries no host-owned-evidence claim.
3. The local OS account, Docker daemon, kernel, admitted image, and filesystem
   authority root remain trusted.
4. Current-head CI, review, merge, and post-merge closure evidence are required
   before `SH-VERIFY-001` or `SH-VERIFY-002` can become `GREEN`.
