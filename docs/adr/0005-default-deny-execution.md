# ADR 0005: Default-Deny Agent Writes And Contained Verification

- Status: Accepted
- Date: 2026-07-21

## Context

The previous direct and queued composition protected only the harness config.
An Agent could replace a test or fixture before verification, and the in-process
verifier could execute candidate code with the operator account. Workspace
mutation detection observed only repository changes made during verification;
it did not protect the oracle or host resources.

## Decision

`ExecutionSettings.trust_mode` defaults to `untrusted-contained`.

- Untrusted Agent runs require at least one operator-declared
  `execution.writable_paths` entry. Every model write must be inside that positive
  allowlist; protected paths take precedence.
- Every untrusted verification snapshots the candidate as a content-addressed
  workspace bundle and sends a digest-bound profile to `DockerVerifierTransport`.
- The container runs non-root with no network, a read-only root filesystem,
  dropped capabilities, resource limits, an exact read-only bundle view, and a
  fresh staging directory.
- Host execution remains available only through the explicit
  `trusted-in-process` mode. A legacy verification-only config additionally
  requires the `--trusted-in-process` command flag.
- Provider structured-output capability is explicit: `json_schema`,
  `json_object`, or `none`. There is no silent fallback.

## Consequences

Docker availability is now a prerequisite for the default direct and queued
runtime. Existing supervised integrations must declare `trusted-in-process`
instead of inheriting host execution. Positive write scope becomes part of the
operator task authority and must exclude tests, fixtures, snapshots, verifier
scripts, configuration, and lifecycle state.

This decision contains candidate execution but does not yet provide a secret
oracle namespace. A later contract adds an independently digested verifier bundle
and makes Control's final adjudication authoritative.
