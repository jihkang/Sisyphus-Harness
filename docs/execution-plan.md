# Local 30.5B Agent Delivery Plan

## Objective

Deliver a repository-local coding agent that runs on the verified Qwen3 30.5B
GGUF model, compacts its context automatically, evolves only its strategy and
cadence through GEPA, and produces reproducible evidence of baseline-to-evolved
improvement. Publish the new harness through a reviewed pull request and finish
the existing Sisyphus P0 security pull request separately.

## Non-negotiable boundaries

- The model has only the six workspace tools. It has no shell, network,
  lifecycle, merge, release, or policy activation authority.
- Verification commands are operator-defined argv arrays and run without a
  shell. Verifier mutation invalidates the result.
- Benchmark obligations are atomic. Each acceptance criterion has one hidden
  verifier projection; the model sees criterion status, not verifier source or
  raw traceback output.
- GEPA may change only `strategy_prompt` and `cadence_policy`.
- GEPA receives train examples only. Holdout examples are evaluated after the
  candidate is fixed and are never reflection inputs.
- A candidate cannot activate itself. Approval and activation require a signed
  operator action.
- No result is called successful unless the persisted verifier receipt passes.
- User-owned changes in any existing worktree must not be reverted or staged.

## Phase 1: Freeze runtime provenance

- [x] Pin the official model revision and file name.
- [x] Verify model size and SHA-256 before loading it.
- [x] Record the llama.cpp version and server flags.
- [x] Confirm the health endpoint and a strict JSON response.
- [x] Persist runtime provenance in the final evidence manifest.

Required evidence:

- Model: `Qwen/Qwen3-30B-A3B-GGUF`
- File: `Qwen3-30B-A3B-Q4_K_M.gguf`
- Expected SHA-256:
  `0d003f6662faee786ed5da3e31b29c978de5ae5d275c8794c606a7f3c01aa8f5`
- llama.cpp build identifier and full server argv
- Harness config hash with secrets and machine-specific paths excluded

Gate: the local server must return healthy and the provider must enforce the
exact agent decision JSON Schema. A failed hash or schema probe blocks every
later phase.

## Phase 2: Establish benchmark baselines

- [x] Make fixture Git state deterministic.
- [x] Exclude runtime artifacts such as `__pycache__` from fixture copies.
- [x] Split compound criteria into atomic hidden verifier commands.
- [x] Prove repeated seed-policy runs have stable scores and workspace hashes.
- [x] Add boundary-heavy train cases so GEPA receives actionable failures
  without receiving holdout examples or diagnostics.
- [x] Complete and retain the final train and holdout baseline from the same
  evolution run that produces the accepted candidate.

Artifacts:

- Baseline aggregate JSON
- Per-case agent result and step receipts
- Per-command verification receipts
- Policy candidate hash
- Train and holdout dataset hashes

Gate: all hard safety gates pass. Baseline failures are allowed, but every
failure must have a complete receipt and no verifier mutation.

## Phase 3: Prove real coding execution paths

### Direct agent run

- [x] Create an isolated Git fixture with a failing deterministic test.
- [x] Run `agent-run` with the real 30.5B provider.
- [x] Confirm at least one bounded workspace mutation.
- [x] Confirm automatic compaction is persisted.
- [x] Confirm final verification passes and records executable hashes.

### Queue worker run

- [x] Initialize queue authority in the Git common directory.
- [x] Submit the same class of task with an idempotency key.
- [x] Process it through `worker-once` using the real provider.
- [x] Confirm lease acquisition, attempt count, terminal status, result linkage,
  and the agent/verifier receipts.
- [x] Retry the idempotency key and prove no duplicate job is created.

Gate: both execution paths must produce an actual modified file and a passing
receipt. A simulated provider or unit-test-only result does not satisfy this
phase.

## Phase 4: Produce an accepted GEPA evolution

- [x] Install and exercise official `gepa==0.1.4` integration.
- [x] Feed bounded action traces and criterion-level outcomes to reflection.
- [x] Normalize local-model wrapper output at the typed component boundary.
- [x] Finish real-model evolution run `qwen30b-gepa-20260718-5`; it was
  correctly rejected because required holdout cases did not pass.
- [x] Diagnose only generic runtime defects from bounded action traces: missing
  initial file discovery, repeated verification on unchanged state, no-op
  mutation reporting, and misunderstood literal search syntax.
- [x] Complete run `qwen30b-gepa-20260718-6`; train reached 100 percent success
  but GEPA returned the unchanged seed and both holdout cases failed. The trace
  exposed compaction-induced loss and confusion of file hashes, so the tool
  response schema and canonical hash context require another correction.
- [x] Complete run `qwen30b-gepa-20260718-7`; GEPA improved train mean from
  `0.706990` to `0.754441`, but holdout mean regressed from `0.013651` to `0.0`,
  so promotion was correctly rejected.
- [x] Confirm the prior llama.cpp process had `--reasoning off`, then restart the
  same model and quantization with `--reasoning on --reasoning-budget 1024`.
- [x] Complete run `qwen30b-gepa-20260718-8`; reasoning raised baseline holdout
  success to 50 percent, but the selected candidate improved train by only
  `0.002878` and regressed holdout mean to `0.047204`, so it was rejected.
- [x] Preserve the bounded canonical working-file content and latest atomic
  criterion status across compaction so the model does not repair stale text.
- [x] Replace binary failed-rollout scoring with latest atomic criterion pass
  rate while retaining full-task success and all holdout promotion gates.
- [x] Restart the same 30.5B model with reasoning enabled and a bounded
  256-token reasoning budget; health and strict JSON Schema probes pass.
- [x] Complete run `qwen30b-gepa-20260718-9`; train mean improved from
  `0.779885` to `0.880296` and holdout mean improved from `0.369408` to
  `0.530921`, but one holdout case failed, so promotion was correctly rejected.
- [x] Remove GEPA reflection commentary and code fences at the typed candidate
  boundary and reject structured strategy metadata.
- [x] Separate canonical source content from JSON context and add line-array
  write/replace modes to preserve literal backslashes in local-model actions.
- [x] Retire the observed v1 holdout to `holdout-v1.json` and freeze a new v2
  holdout before its first model evaluation.
- [x] Complete run `qwen30b-gepa-20260718-10`; train mean improved from
  `0.512582` to `0.892056`, but holdout mean regressed from `0.572204` to
  `0.220066` and neither candidate holdout case passed, so promotion was
  correctly rejected.
- [x] Retire the observed v2 holdout without editing or reusing it. Its traces
  exposed two general runtime defects: repeated no-op repair attempts and
  two-state workspace oscillation after an execution error.
- [x] Classify verifier failures into bounded, non-secret categories and tell
  the agent when verification could not execute assertions normally, without
  exposing verifier source, stdout, stderr, exception text, or hidden names.
- [x] Detect repeated workspace-state cycles under an unchanged criterion
  signature, warn after the first revisit, allow a repair into a new state, and
  stop at the configured stagnation threshold.
- [x] Freeze a new v3 holdout before its first model evaluation and run it once;
  do not expose holdout examples or verifier contents to GEPA.
- [x] Complete run `qwen30b-gepa-20260718-11`; train mean improved from
  `0.686497` to `0.917105`, holdout mean improved from `0.718388` to
  `0.952303`, holdout success improved from `0.50` to `1.00`, and all hard
  gates passed.
- [x] Obtain an accepted candidate with measurable train and holdout deltas.

Acceptance gates:

- Candidate train mean is at least baseline train mean plus `min_train_delta`.
- Candidate holdout mean is at least baseline holdout mean plus
  `min_holdout_delta`.
- Both configured deltas must be greater than zero; a tie is not measurable
  improvement and cannot be promoted.
- Candidate holdout success rate does not regress.
- Every required holdout case passes.
- All train and holdout hard gates pass.
- Candidate contains only a plain strategy component and schema-valid cadence.

The result must include GEPA engine metadata, proposal budget, seed, candidate
hash, baseline aggregates, candidate aggregates, and rollout paths.

## Phase 5: Approve, activate, and revalidate

- [x] Review the accepted `result.json` and candidate diff.
- [x] Run `policy-approve` with an explicit operator note.
- [x] Verify the HMAC signature before activation.
- [x] Run `policy-activate` and verify the signed active-policy pointer.
- [x] Run train and holdout benchmarks with `--policy active`.
- [x] Run one additional direct agent task with `--policy active`.

Gate: active-policy scores must reproduce the accepted candidate within the
deterministic benchmark contract. Signature mismatch, candidate mismatch, or a
failed case blocks publication.

## Phase 6: Build the evidence bundle

- [x] Add a machine-readable evidence manifest under
  `evidence/qwen30b-final/manifest.json`.
- [x] Add a concise human-readable report under
  `evidence/qwen30b-final/README.md`.
- [x] Hash every referenced config, dataset, policy, aggregate, and receipt.
- [x] Record model provenance and llama.cpp version without copying the model.
- [x] Redact API keys, local usernames, absolute home paths, and hidden verifier
  source.
- [x] Verify every manifest path exists and every digest matches.

The report must show baseline and evolved train/holdout mean scores, success
rates, step counts, compaction counts, hard-gate status, direct-run result, and
queue-run result. Rejected evolution attempts may be summarized separately and
must never be presented as accepted evidence.

## Phase 7: Final repository verification

- [x] Run the complete unit test suite.
- [x] Enforce at least 90 percent branch coverage.
- [x] Run the real installed-GEPA integration test.
- [x] Run `compileall` and `git diff --check`.
- [x] Run `uv lock --check`.
- [x] Build sdist and wheel offline.
- [x] Install the wheel into a fresh virtual environment.
- [x] Execute CLI smoke tests from the installed wheel.
- [x] Confirm repository status contains no generated caches or model files.

Gate: every check passes from a clean copy. Any test that depends on the
developer checkout instead of the installed wheel must be corrected.

## Phase 8: Publish the new harness repository

- [x] Copy the project from `/tmp/Sisyphus-Harness` to the durable
  `$HARNESS_WORKSPACE`, excluding virtualenvs, build products, caches, and
  model files.
- [ ] Initialize or create the GitHub repository if it does not exist.
- [x] Establish a minimal `main` baseline commit when required for PR review.
- [ ] Before creating the feature branch, run `git switch main`,
  `git fetch origin main`, and `git pull --ff-only`.
- [ ] Create a `codex/` feature branch.
- [ ] Stage only scoped harness files and evidence.
- [ ] Commit, push, and open a ready PR.
- [ ] Wait for every required CI check and inspect failures before changing code.
- [ ] Merge only after CI is green.
- [ ] Switch to `main`, fetch, and fast-forward pull after merge.
- [ ] Record the merged commit and PR URL in the evidence report.

Gate: the remote default branch contains the merged commit and the local main
matches `origin/main`.

## Phase 9: Finish the existing Sisyphus P0 security change

Existing branch: `codex/legacy-p0-safety-hardening`

Existing commit: `9ee1e84 Harden repository path and Discord boundaries`

- [x] Use the authenticated GitHub connector for PR and merge operations.
- [x] In the legacy worktree, confirm only the intended P0 changes are present.
- [x] In a clean temporary worktree, switch the base repository to `main`,
  fetch `origin/main`, and run
  `git pull --ff-only` without touching user-owned dirty files.
- [x] Confirm no rebase or merge was required because the branch was based on
  the current `origin/main`.
- [x] Re-run the legacy 497-test, coverage, build, and diff checks.
- [x] Create PR #63, wait for CI run 29608344012, and squash merge it.
- [x] Fetch and fast-forward local main after merge.
- [x] Confirm local and remote main both resolve to
  `742dd4354bf0c123549f2992f7e9303a0c594bc1`.

Gate: the P0 PR is merged independently of the new harness PR. No harness file
is mixed into the legacy security change.

## Phase 10: Post-implementation code review

- [x] Review authority, queue, workspace, agent, verifier, benchmark, evolution,
  policy, CLI, packaging, CI, and evidence boundaries.
- [x] Fix criterion-to-verifier binding, policy path containment, independent
  holdout enforcement, provider response bounds, and finite lease handling.
- [x] Record resolved findings, open risks, abstraction debt, performance
  bottlenecks, and test gaps in `docs/code-review-2026-07-18.md`.
- [x] Re-run the real 30.5B active policy on final reviewed code.
- [x] Rebuild and verify the evidence manifest after final-code revalidation.

Gate: resolved findings pass regression tests. Open P1 risks remain explicit
scope limits and block untrusted, multi-tenant, or concurrent-worker claims.

## Known blockers and handling

- GitHub CLI authentication is expired. The authenticated GitHub connector was
  sufficient for the existing Sisyphus PR, but it cannot create a repository.
  Creating the new Harness remote therefore requires an authenticated GitHub
  browser session or restored CLI authentication.
- The durable harness remote does not yet exist. Create it only after the
  evidence and clean-build gates pass, then resume Phase 8 in the listed order.
- The original Sisyphus base worktree contains user changes. Never reset,
  restore, clean, or stage those files.
- Rejected GEPA runs are expected evidence of gate enforcement. Continue only
  after correcting a generalizable defect; do not lower holdout or safety gates
  to force acceptance.
- Holdout v1 and v2 are spent development splits. V3 is the final one-shot
  promotion split for this delivery: after first observation, do not edit,
  replace, or rerun it to search for a passing result. A v3 rejection remains a
  truthful blocked outcome unless the operator authorizes a new experimental
  protocol with a precommitted reserve-set procedure.

## Definition of done

All phases are complete only when the real 30.5B model has produced passing
direct and queued coding results, an operator-approved active policy shows a
measurable baseline-to-evolved improvement on train and holdout, the evidence
manifest verifies, all package checks pass, both pull requests are merged, and
local `main` branches are synchronized with their remotes.
