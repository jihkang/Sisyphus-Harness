# Remediation Roadmap

## Dependency Order

```text
Default-deny writes and contained verifier
  -> AttemptFinished persistence
  -> Control-only TaskOutcome
  -> immutable oracle/profile binding
  -> module and shared-primitive cleanup
  -> typing/race/crash hardening
  -> benchmark and release evidence
  -> repository governance
```

## Reviewable PR Slices

| Slice | Scope | Must not include |
| --- | --- | --- |
| A | response schema, provider capability, write allowlist, bundle verifier default, evidence classification | queue schema redesign |
| B | AttemptFinished and TaskOutcome schema, Control transaction, CLI status projection | benchmark changes |
| C | verifier-owned asset bundle, full digest binding, and host-owned command evidence | evolution policy |
| D | shared primitives and CLI/Agent/Worker/Verifier decomposition | behavior changes |
| E | strict typing, property/race/crash tests, platform CI | new product features |
| F | repeated benchmark, current evidence, SBOM and governance | historical evidence rewriting |

Each slice starts from fetched `main`, carries focused regression tests, and is
merged only after checks and review target the current head SHA.

## Current Delivery State

- Slice A merged in PR #8 at `77cd48e` with all required CI jobs passing.
- Slice B merged in PR #9 at `8cccfef` after all five current-head CI jobs
  passed. Its gates cover schema migration, stale-attempt fencing, immutable
  rows, idempotent Control publication, contained composition, and CLI
  projection.
- Slice C is implemented on its review branch with local and real-Docker gates
  passing, including per-command containers without authority mounts and
  host-created receipts; current-head CI and merge evidence remain pending.
- Slices D through F remain open and must start from refreshed `main` after the
  preceding slice is merged.

The living [implementation debt register](../../status/implementation-debt.md)
assigns stable IDs and exit conditions to the open work in these slices. This
dated roadmap defines dependency order; the register owns current status.
