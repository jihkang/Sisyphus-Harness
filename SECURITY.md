# Security Policy

Sisyphus Harness is experimental. Default direct and queued execution applies a
positive write allowlist and Docker-contained verification, but it is not a
multi-tenant or secret-oracle sandbox. Run it only on repositories and machines
where the operator accepts the configured local model and verifier commands.

## Supported Versions

Security fixes are applied to the current `0.1.x` development line.

## Reporting

Do not disclose a suspected vulnerability in a public issue. Use GitHub private
vulnerability reporting:

`https://github.com/jihkang/sisyphus-harness/security/advisories/new`

Include the affected commit, platform, configuration, reproduction steps,
expected boundary, observed behavior, and whether any path outside the target
repository was modified.

## Trust Boundaries

Operator-controlled:

- repository selection and configuration;
- local model endpoint and credentials;
- verifier argv and acceptance criteria;
- benchmark fixtures and hidden verifier programs;
- approval and activation of evolved policies;
- Git commit, push, review, merge, and release.

Model-controlled:

- JSON decisions in the bounded coding loop;
- repository-local file reads, searches, and approved mutations;
- proposed strategy and cadence text during offline evolution.

The model is not given shell, network, Git, lifecycle, queue, policy, approval,
merge, or release tools. In the default `untrusted-contained` mode, every model
write must also fall under an operator-declared `execution.writable_paths`
entry. Repository paths are resolved before use, Git, authority state, and the
configuration loaded for the run are protected from model writes, existing
writes require stale-content hashes, and verification fails if its command
changes workspace state.

The file tools are a direct-tool boundary, not a process sandbox. In explicit
`trusted-in-process` mode and the current benchmark/evolution compatibility
path, a verifier may execute model-edited source with the host process's
filesystem, environment, and network privileges. Workspace mutation detection
records repository changes after execution; it does not prevent or undo writes
outside the repository. Do not use those host paths for untrusted code.

`DockerVerifierTransport` is the supplied external verifier boundary. It uses a
read-only root filesystem, disables networking, drops capabilities, and bounds
CPU, memory, process count, and combined Docker stdout/stderr. The host rejects
symlinked, digest-invalid, substituted, or concurrently replaced source objects,
resolves and rechecks the Docker image ID, and executes that ID instead of the
mutable tag. Each command is PID 1 of a separate container and receives only a
fresh materialized workspace read-write plus the exact content-addressed verifier
asset tree read-only when requested. It receives no request file, bundle CAS,
writable evidence staging, authoritative evidence root, Control database, or
signing key. Output overflow or timeout kills the Docker client process group and
force-removes its CID. The trusted host writes stdout/stderr, command results,
the v3 receipt, and the service result, then validates workspace, profile, image,
asset, request, command, and artifact bindings before atomic publication. This is
the default adapter for full harness configurations in direct and queued runs.
It does not turn model inference or an explicitly selected in-process verifier
into a sandbox, and it is not a multi-tenant isolation claim. Mounted verifier
assets remain readable by the command. Secret oracle assets require a separate
evaluator process or VM that does not expose those bytes to candidate code.

The standalone Compose entry point is a compatibility executor, not the default
host-owned evidence topology, and cannot perform the transport's host-side CAS
copy. Its `SISYPHUS_BUNDLE_STORE` must therefore point to a fresh directory that
contains only the exact validated request archive/reference pair, and its staging
mount must be fresh and non-authoritative. Mounting the full CAS weakens bundle
confidentiality even though the mount remains read-only.

## Operational Requirements

- Bind local model servers to loopback unless a separate authenticated network
  boundary is in place.
- Keep API keys in environment variables, not repository configuration.
- Build and inspect the configured verifier image before direct or unattended
  worker execution; the default trust mode fails if Docker is unavailable.
- Review verifier commands before every unattended worker deployment.
- Freeze reviewed verifier files with `verifier-assets-create` and use the exact
  returned bundle ID when creating a final Control profile. Treat any replacement
  as a new profile and evidence contract.
- Run at most one coding worker per repository unless an external repository
  mutation lock or isolated worktree layer is in place. Queue leases fence the
  terminal database transition, not side effects from a worker that continues
  after losing its lease.
- Keep provider and verifier timeouts below the configured agent runtime budget.
  One monotonic deadline is propagated through the built-in provider, tools,
  and verifier. A third-party port that does not implement the deadline-aware
  protocol can still overrun while blocked and must be treated as trusted.
- Keep benchmark verifier programs outside agent workspaces. Until benchmark
  composition uses the contained runtime, run benchmark/evolution only in a
  separately sandboxed environment.
- Give every manual Compose verifier invocation a new empty
  `SISYPHUS_VERIFICATION_STAGING` directory; never mount the authoritative
  evidence root read-write into the container.
- Inspect evolution train and holdout receipts before approval.
- Back up the Git common directory if queue, policy, and receipt history must be
  retained.
- Treat a lost `policies/authority.key` as loss of activation provenance.

## Out Of Scope

The harness does not defend against a malicious operating-system account,
kernel, container runtime, Git executable, Python interpreter, verifier image,
or model server. Commands used by explicit in-process and benchmark/evolution
compatibility paths execute with the operator's account and must be treated as
trusted code.
