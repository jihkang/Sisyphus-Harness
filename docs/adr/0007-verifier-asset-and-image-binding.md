# ADR 0007: Verifier Asset And Image Integrity Binding

- Status: Accepted, amended by ADR 0008
- Date: 2026-07-21

## Context

Contained verification already received an immutable candidate workspace, but
the verification profile did not identify operator-owned scripts or fixtures.
The Docker transport also accepted a mutable image tag. A receipt could bind the
workspace and profile while leaving the command assets and actual verifier image
outside the transitive evidence chain.

A read-only mount solves a different problem from a secret oracle. It prevents
candidate code from changing mounted bytes, but code in the same container mount
namespace can still read them. The architecture must not use an integrity
control as evidence of confidentiality.

## Decision

1. Operator-owned verifier files are frozen in a deterministic, content-addressed
   `VerifierAssetBundleRef`. The store accepts regular files only, rejects
   symlinks and special entries, applies byte and entry limits, and validates
   stable file identity, manifest SHA-256, file SHA-256, and tree hash before
   materialization.
2. `VerificationProfile.v2` binds the exact asset reference. Agent verification
   may use a v2 profile without assets, but final Control adjudication requires a
   non-null verifier asset bundle.
3. The host transport resolves a configured Docker reference to an immutable
   image ID before request admission, checks the mapping again immediately before
   execution, and passes the image ID, not the mutable tag, to `docker run`.
4. `BundleVerificationRequest.v2`, `VerificationServiceResult.v2`, and
   `VerificationReceipt.v3` bind the workspace bundle, profile, execution
   identity, and verifier asset bundle. `TaskOutcome.v2` snapshots the profile
   and execution identity and binds the request and receipt digests.
5. The host creates fresh exact workspace and verifier-asset views. The default
   transport gives each command the workspace read-write and the asset tree
   read-only, hashes both around execution, and publishes only host-created
   evidence. ADR 0008 defines this amended execution topology.
6. The in-container `VerificationExecutorPort` executes an already admitted
   request. Only the host-side `VerificationServicePort` resolves runtime
   identity. An executor that cannot establish image identity cannot be injected
   directly into Control.
7. This decision makes no secret-oracle claim. Confidential checks require a
   separate evaluator process or VM and a narrow result protocol that never
   exposes oracle bytes to candidate code.

## Consequences

- Asset, profile, request, image, receipt, workspace, and outcome substitution
  can be rejected independently.
- A moved Docker tag cannot change the admitted runtime after the request is
  constructed.
- Verifier assets are immutable inputs, not candidate workspace content.
- The standalone same-container compatibility deployment remains suitable for
  integrity-protected checks, not confidential hidden tests. The default host
  transport isolates authoritative evidence from each candidate container.
- Legacy v1 profile/request/result and v2 receipt decoding remains available for
  compatibility, but current Control publication requires the new schemas.
