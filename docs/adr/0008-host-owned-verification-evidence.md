# ADR 0008: Host-Owned Verification Evidence

- Status: Accepted
- Date: 2026-07-22
- Amends: ADR 0007

## Context

The first contained verifier topology ran `BundleVerifierService` and candidate
commands under one container UID and mount namespace. The service needed a
writable evidence staging mount, so candidate code could reach the same staging
tree. Process-group cleanup handled ordinary descendants, but a candidate could
start a new session, close inherited pipes, and race service-owned files before
container PID 1 exited.

Host digest checks detect accidental corruption. They are not an authority
boundary when the process being checked can reach both the staged bytes and the
result channel. Verification evidence must therefore be constructed outside the
candidate command's mount and process namespace.

## Decision

1. `DockerVerifierTransport` materializes the admitted workspace and verifier
   asset bundles in a private host temporary directory.
2. Every `CommandSpec` runs as PID 1 of a separate ephemeral container using the
   admitted immutable image ID. The command receives only `/workspace`
   read-write and, when requested, `/verifier-assets` read-only. It receives no
   service request, bundle CAS, evidence root, or evidence staging mount.
3. A separate metadata-only invocation resolves and hashes `argv[0]` in the same
   image. Probe failure cannot produce a passing command result.
4. The trusted host transport owns the global deadline, combined output limit,
   CID cleanup, workspace and asset snapshots, stdout/stderr artifacts,
   `CommandResult`, `VerificationReceipt.v3`, and
   `VerificationServiceResult.v2` construction.
5. Normal non-zero exit, timeout, output overflow, launch failure, or workspace
   mutation produces bounded failed evidence. Docker infrastructure failure or
   invalid source/evidence state remains a transport error and publishes no run.
6. The host validates the complete request, workspace, profile, image, asset,
   command, receipt, and artifact bindings before committing an authoritative
   run. The request record is written before the run-directory commit and is
   removed if that commit fails.
7. `BundleVerifierService` and `VerificationExecutorPort` remain compatibility
   surfaces for explicit in-process and standalone Compose use. They are not the
   default Agent or Control verification topology and do not inherit this
   candidate/evidence isolation claim.

## Consequences

- Candidate stdout is diagnostic input to the host, not a service-result
  protocol. Candidate JSON cannot mint or substitute a receipt.
- A detached descendant cannot outlive its direct PID 1 container or retain an
  evidence mount because no such mount exists.
- Verifier assets remain visible to a command that must execute or read them.
  This decision protects evidence ownership and asset integrity, not confidential
  hidden-oracle bytes.
- The default path creates one metadata container and one command container per
  `CommandSpec`. Startup cost is accepted in exchange for a smaller authority
  surface; batching is not allowed to reintroduce shared writable evidence.
- Wire schema versions remain unchanged. Compatibility decoding does not imply
  compatibility execution is equally isolated.

## Rejected Alternatives

- File modes or a second UID in one container do not provide a durable boundary
  against namespace-level staging access.
- Signing candidate-written evidence does not help because the signing process
  would still consume attacker-controlled staged bytes.
- Parsing the last stdout line as `VerificationServiceResult` lets candidate
  output participate in authority and is no longer used by the default path.
