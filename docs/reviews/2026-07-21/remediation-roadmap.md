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
| C | verifier-owned oracle bundle and full digest binding | evolution policy |
| D | shared primitives and CLI/Agent/Worker/Verifier decomposition | behavior changes |
| E | strict typing, property/race/crash tests, platform CI | new product features |
| F | repeated benchmark, current evidence, SBOM and governance | historical evidence rewriting |

Each slice starts from fetched `main`, carries focused regression tests, and is
merged only after checks and review target the current head SHA.
