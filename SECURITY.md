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
  The runtime budget is checked between agent steps and does not preempt an
  already-blocked provider or verifier operation.
- Keep benchmark verifier programs outside agent workspaces.
- Inspect evolution train and holdout receipts before approval.
- Back up the Git common directory if queue, policy, and receipt history must be
  retained.
- Treat a lost `policies/authority.key` as loss of activation provenance.

## Out Of Scope

The harness does not defend against a malicious operating-system account,
kernel, Git executable, Python interpreter, verifier command, model server, or
code executed by a verifier. Verifier commands intentionally execute with the
operator's account and must be treated as trusted code.
