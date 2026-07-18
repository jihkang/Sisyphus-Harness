# Offline Evolution

The evolution subsystem follows the useful part of Hermes Agent's design:
collect real traces, reflect on failures, evolve a narrow policy surface, test
on held-out cases, and leave deployment to a human-controlled gate.

## Evolvable Surface

Each candidate contains exactly two textual fields:

- `strategy_prompt`;
- `cadence_policy`, encoded as strict JSON.

Cadence values control compaction, observation, reflection, intermediate
verification, retained events, context size, and stagnation detection. Every
field is parsed into `CadencePolicy`, which enforces hard numeric bounds.

Unknown, missing, non-integer, boolean, or out-of-range fields are rejected.

## Immutable Surface

Evolution cannot modify:

- the safety prompt;
- available tools or argument schemas;
- path containment and stale-write checks;
- runtime, step, file, output, protocol-error, and compaction ceilings;
- verification argv, criteria, timeout enforcement, and mutation checks;
- queue leases or terminal-state rules;
- signing keys, approval receipts, or activation logic.

This keeps evolution focused on how often the harness intervenes and what
strategy the model follows, rather than allowing it to weaken enforcement.

## Evaluation

For each candidate, a benchmark rollout:

1. copies the visible fixture into a fresh directory;
2. initializes a new Git repository;
3. runs the bounded agent;
4. invokes an external hidden verifier;
5. stores complete agent and verification receipts;
6. computes correctness, step-efficiency, and compaction-efficiency scores.

Failures are capped at a low score. Verifier mutation and tool mutation mismatch
are hard-gate failures.

GEPA receives structured diagnostics as actionable side information. After
optimization returns a candidate, the harness reruns both training and holdout
sets independently. A candidate is rejected for insufficient score delta,
holdout success regression, any required holdout failure, or a failed hard gate.
Training and holdout example IDs must be disjoint, and both configured score
deltas must be strictly positive.

## Activation

An accepted result has status `proposed`, not `active`.

`policy-approve` binds the candidate hash, evolution ID, operator username, UID,
hostname, note, and timestamp into an HMAC-SHA256 receipt. `policy-activate`
verifies that receipt and writes a signed active policy. Tampering with the
candidate, approval, or active policy is detected on load.
