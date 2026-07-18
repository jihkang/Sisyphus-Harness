# Code Review - 2026-07-18

## Verdict

The pre-publication implementation is suitable for supervised, single-user,
single-worker experimentation. It is not a process sandbox and is not ready for
untrusted models, multi-tenant execution, or concurrent workers mutating one
repository. Those limits are explicit in `SECURITY.md` and are release
constraints rather than implied guarantees.

The review covered authority storage, queue leases, workspace containment,
agent orchestration, verifier execution, benchmark isolation, GEPA evolution,
policy signing, CLI dispatch, packaging, CI, and evidence integrity.

## Resolved Findings

### CR-H1: Task criteria were not bound to verifier criteria

`LocalCodingAgent.run()` previously accepted any passing configured verifier,
even when none of its criteria matched the task acceptance criteria. This could
produce a successful run receipt for an unrelated requirement.

Resolution: task criteria are normalized and unique, and every task criterion
must now appear in at least one selected `CommandSpec`. Regression coverage was
added in `tests/test_agent.py`, with CLI and worker fixtures aligned to the
contract.

### CR-H2: A forged evolution ID could escape the approval path

`PolicyRegistry.approve()` used the evolution ID from an input JSON artifact in
an output filename without validating it. A crafted `../../...` value could
write a signed receipt outside `policies/approvals`.

Resolution: evolution IDs use one shared validator, CLI result lookup is
contained under the evolution artifact root, and approval output is contained
under the approval root. A traversal regression test was added.

### CR-H3: The evolution contract allowed invalid holdout protocols

The runner did not reject overlapping train and holdout IDs, and score delta
thresholds could be zero. Both behaviors contradicted the claimed independent,
measurable promotion gate.

Resolution: both partitions require unique, disjoint IDs and both deltas must be
finite and in the interval `(0, 1]`. Unit and configuration tests cover
overlap, duplicates, zero, non-finite, and out-of-range thresholds.

### CR-M1: Provider responses were read without a byte ceiling

A faulty local endpoint could return an arbitrarily large response before JSON
validation or model-output limits applied.

Resolution: the HTTP body is capped at 16 MiB and oversized responses fail
closed before parsing.

### CR-M2: Non-finite lease values could strand queue jobs

`NaN` and infinite lease durations passed a simple positive comparison and
could create a `running` row that no worker could reclaim.

Resolution: lease duration, clock, and computed expiry must be finite. The
heartbeat interval is also guaranteed to occur before half of even a very short
lease.

### CR-M3: CI did not consume the lock or verify evidence/package gates

CI installed dependencies independently with pip, so `uv.lock` did not govern
the tested environment. It also omitted manifest, compile, build, and installed
wheel checks.

Resolution: CI uses an exact `uv` version with `--frozen`, retains separate core
and GEPA jobs, and adds manifest, lock, compile, diff, offline build, and wheel
installation smoke checks. Whitespace validation compares a pull request with
its base SHA and checks the pushed commit on `main`; it does not inspect an
always-empty clean-checkout diff.

## Open Risks

### CR-O1 (P1): Verification is trusted code, not a sandbox

`BoundedVerifier` launches operator argv with the operator environment. A test
command may import model-edited source, which then has the verifier process's
filesystem and network privileges. Workspace snapshots detect repository
mutation after execution; they cannot prevent external writes or secret access.

Required control: execute verifiers in a container, VM, or OS sandbox with a
minimal environment, read-only mounts outside the workspace, and disabled
network. Until then, malicious models and repositories remain out of scope.

### CR-O2 (P1): Queue leases do not fence repository side effects

Lease ownership atomically fences heartbeat and terminal database writes. If a
worker is suspended past expiry and later resumes, it can continue mutating the
repository while the replacement worker runs. The stale worker cannot complete
the job, but its filesystem effects are not revoked.

Required control: allocate an isolated worktree per attempt and promote changes
through a fencing token, or hold an external per-repository mutation lock for
the complete run. Operate one coding worker per repository until this exists.

### CR-O3 (P1): The agent runtime budget is not a hard deadline

`max_runtime_seconds` is checked between loop steps. An in-flight provider call,
Git snapshot, file operation, or verifier command can exceed the remaining
budget up to its own timeout.

Required control: propagate one monotonic deadline through provider, workspace,
and verifier APIs and cap every blocking operation to the remaining duration.

### CR-O4 (P2): Verifier provenance hashes only argv[0]

For `python verify.py`, receipts hash the Python executable but not `verify.py`
or imported verifier assets. Benchmark manifests compensate by hashing fixture
files, while general direct-run receipts do not.

Required control: support declared verifier input artifacts and bind their
digests into each command receipt.

### CR-O5 (P2): Workspace snapshots dominate orchestration overhead

One snapshot starts six Git subprocesses. Agent steps take multiple snapshots,
and each verifier command adds two more. A 20-step rollout can therefore launch
hundreds of Git processes before GEPA multiplies the rollout count.

Recommended change: derive state from one bounded porcelain-v2 status call plus
targeted content hashing, and reuse snapshots when no external operation could
have changed the workspace.

### CR-O6 (P2): Text search is an O(repository bytes) Python scan

`search_text` opens every tracked and untracked UTF-8 file for each query. This
is predictable but becomes a bottleneck on large repositories.

Recommended change: use a proven literal-search backend such as `rg --json`
with explicit path containment, result, byte, and timeout limits.

### CR-O7 (P2): Artifact retention is unbounded

Every step stores complete prompts, model responses, snapshots, verifier output,
and copied benchmark rollouts. Long evolution runs can consume substantial disk
and retain sensitive source indefinitely.

Recommended change: add size/age quotas, explicit retention classes, and a
receipt-preserving prune command.

### CR-O8 (P2): HMAC approval is local integrity, not external identity

The HMAC key and signed policy live under the same user-owned Git common
directory. This prevents model file tools and accidental edits from silently
changing policy, but any process with the operator account can read the key and
forge approvals.

Recommended change: optionally support an OS keychain or asymmetric signing key
outside repository authority storage. Keep same-account compromise out of scope
until then.

### CR-O9 (P3): Package builds are functional but not byte-reproducible

Repeated successful offline builds can produce different wheel and sdist hashes
because archive timestamps are not normalized.

Recommended change: set and document `SOURCE_DATE_EPOCH`, normalize archive
metadata, and compare two independent build hashes in CI.

## Abstraction Review

Under-abstracted areas:

- `agent.py` combines context construction, cadence, state-cycle detection,
  provider calls, tools, verifier orchestration, and receipt persistence in one
  large state machine. Split only after the behavioral contract is frozen.
- Benchmark and evolution boundaries exchange broad `dict[str, Any]` payloads.
  Typed example and diagnostic models would move schema failures to construction
  time and reduce key drift.
- Subprocess execution policy is repeated across authority, workspace,
  benchmark, and verifier modules with different timeout and environment rules.

No current abstraction is materially over-engineered. `Database`,
`ChatProvider`, and `EvolutionEngine` are thin, but each isolates a real
transaction, integration, or test boundary. Removing them would increase
coupling without reducing meaningful complexity.

## Test Gaps

- no multi-process test demonstrates stale-worker filesystem fencing;
- no global-deadline integration test covers provider plus verifier time;
- no sandbox escape test exists because sandboxing is not implemented;
- no large-repository benchmark measures snapshot and search scaling;
- no double-build test enforces byte-for-byte package reproducibility;
- GitHub-hosted CI remains unobserved until the new remote is created.

## Publication Gate

Resolved findings must remain green in the full unit suite, branch coverage,
installed GEPA integration, evidence manifest verification, and installed-wheel
smoke test. Open P1 risks are acceptable only under the supervised experimental
scope documented above; broad production claims remain blocked.

## Post-Review Revalidation

The final reviewed code was exercised with the same Qwen3 30.5B model and the
unchanged signed active candidate. Train retained a `+0.210822` mean-score
improvement over baseline with `0.75` success. Frozen holdout-v3 retained a
`+0.220757` improvement with every case passing. Fresh direct and idempotent
queue proofs each completed in 3 steps with one automatic compaction and one
passing final verification. Redacted receipts are stored under
`evidence/qwen30b-final/artifacts/final-code-review`.

The post-review suite completed 139 tests with 91.6 percent branch-aware
coverage. Installed GEPA integration, compilation, lock validation, offline
sdist/wheel construction, isolated wheel import, CLI help, and authority
initialization also passed.

A separate clean clone received only the intended publishable files. After
staging them, cached-diff, pull-request base-diff, and pushed-commit whitespace
checks passed. The same clone completed all 139 core tests, retained 91.6
percent coverage, and verified all 119 evidence-manifest entries.
