# Security Policy

Sisyphus Harness is experimental and is not a process sandbox. Run it only on
repositories and machines where the operator accepts the configured local model
and verifier commands.

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
merge, or release tools. Repository paths are resolved before use, Git,
authority state, and the configuration loaded for the run are protected from
model writes, writes require stale-content hashes, and verification fails if its
command changes workspace state.

This is a direct-tool boundary, not a process sandbox. A verifier may import or
execute source that the model changed. That code then runs with the verifier
process's filesystem, environment, and network privileges. Workspace mutation
detection records repository changes after execution; it does not prevent or
undo writes outside the repository. Use a container, VM, or equivalent external
sandbox for untrusted models or repositories.

`DockerVerifierTransport` is the supplied external verifier boundary. It uses a
read-only root filesystem and an isolated read-only view containing only the
requested bundle archive/reference pair, disables networking, drops capabilities,
bounds CPU, memory, process count, and combined Docker stdout/stderr, and exposes
only a fresh writable staging directory. The host rejects symlinked, digest-invalid,
or concurrently replaced source bundle objects before launch. Output overflow or
timeout kills the Docker client process group and force-removes its CID. The host
validates the staged receipt reference and bytes before atomically publishing it
to the authoritative evidence root. This
does not turn the in-process verifier or the coding Agent into a sandbox, and it
is not a multi-tenant isolation claim. The service and test command share one
container UID and mount namespace; the Docker boundary therefore does not hide
the current request/profile or writable staging paths from that command. Put
secret oracle assets behind a separate command sandbox or verifier-only
namespace.

The standalone Compose entry point cannot perform the transport's host-side CAS
copy. Its `SISYPHUS_BUNDLE_STORE` must therefore point to a fresh directory that
contains only the exact validated request archive/reference pair, and its staging
mount must be fresh and non-authoritative. Mounting the full CAS weakens bundle
confidentiality even though the mount remains read-only.

## Operational Requirements

- Bind local model servers to loopback unless a separate authenticated network
  boundary is in place.
- Keep API keys in environment variables, not repository configuration.
- Review verifier commands before every unattended worker deployment.
- Run at most one coding worker per repository unless an external repository
  mutation lock or isolated worktree layer is in place. Queue leases fence the
  terminal database transition, not side effects from a worker that continues
  after losing its lease.
- Keep provider and verifier timeouts below the configured agent runtime budget.
  One monotonic deadline is propagated through the built-in provider, tools,
  and verifier. A third-party port that does not implement the deadline-aware
  protocol can still overrun while blocked and must be treated as trusted.
- Keep benchmark verifier programs outside agent workspaces.
- Give every manual Compose verifier invocation a new empty
  `SISYPHUS_VERIFICATION_STAGING` directory; never mount the authoritative
  evidence root read-write into the container.
- Inspect evolution train and holdout receipts before approval.
- Back up the Git common directory if queue, policy, and receipt history must be
  retained.
- Treat a lost `policies/authority.key` as loss of activation provenance.

## Out Of Scope

The harness does not defend against a malicious operating-system account,
kernel, Git executable, Python interpreter, verifier command, model server, or
code executed by a verifier. Verifier commands intentionally execute with the
operator's account and must be treated as trusted code.
