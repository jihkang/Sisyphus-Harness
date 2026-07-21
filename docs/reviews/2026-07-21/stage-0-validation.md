# Stage 0 Validation Record

This record covers the Stage A working tree based on
`26e141d360dceddc984ca506ec5ed0daaccde6d4`. GitHub CI on the eventual PR head
and post-merge `main` remain the authority for integration status.

## Passed Locally

| Gate | Result |
| --- | --- |
| Full branch-aware suite | 370 tests passed, 1 opt-in Docker test skipped |
| Coverage | 91.1%, above the 90% gate |
| Oracle regression | mutation outside the source allowlist rejected; source repair passed |
| Real Docker boundary | non-root, zero effective capabilities, no-new-privileges, read-only root, no external network, exact two-object bundle view, unchanged workspace |
| Static analysis | Ruff passed; Bandit reported no medium/high findings |
| Evolution package | installed GEPA integration passed |
| Historical evidence | 126 files verified; `claim_scope=historical`; `source_matches_head=false` |
| Packaging | frozen lock and compile passed; offline sdist/wheel built; isolated wheel import and CLI smoke passed |

The real-container test is committed as
`tests/test_docker_verifier_integration.py` and runs in CI after building the
current verifier image. Unit tests also prove that the Agent deadline clamps the
Docker transport timeout and that unsafe image arguments are rejected.

## Not Yet Proven

- No local model endpoint was listening on the configured Qwen ports. The
  root-object strict schema is covered by contract/provider tests, but acceptance
  by a real supported endpoint remains open.
- The 30.5B bundle is historical evidence for `47539e0`; no current-release model
  performance or security claim is made.
- Worker terminal state is still derived from `AgentResult.success`; Control-only
  semantic `TaskOutcome` is Stage B.
- Verifier commands and hidden assets still share one container identity and mount
  namespace; secret-oracle isolation is Stage C.
- Repository branch protection and required-review settings must be checked and
  enabled through GitHub after the focused PR is merged.

These open items prevent the baseline 43/100 review score from being replaced by
a production-readiness claim.
