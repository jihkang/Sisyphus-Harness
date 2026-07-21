# Sisyphus Harness

Sisyphus Harness is a repository-local control plane for bounded coding agents.
It runs an OpenAI-compatible local model, limits the model to six file tools,
verifies changes with operator-defined commands, and stores replayable receipts
under the Git common directory.

This repository is a rewrite. It does not preserve the original Sisyphus public
API or task-file format.

## Status

The project is experimental and intended for supervised local use. It provides:

- transactional SQLite jobs with idempotency and expiring worker leases;
- repository-contained read, search, write, replace, and delete tools;
- stale-write hashes, atomic writes, and protected Git/authority paths;
- write protection for the operator configuration used by a direct or queued run;
- structured verification commands with no shell interpolation;
- timeouts, process-group termination, full output, executable hashes, and
  workspace-mutation detection;
- immutable evidence contracts with bounded three-valued predicate evaluation;
- Control-owned shadow adjudication over the exact Worker output bundle, independent
  of the coding agent's reported success;
- a rebuildable, candidate-only SQLite knowledge graph with anchored depth-three
  search, dependency inspection, and deterministic next-step context;
- a strict JSON coding loop with automatic deterministic compaction;
- hidden-fixture benchmark evaluation;
- Hermes-style offline GEPA evolution of only the strategy prompt and cadence;
- signed operator approval before an evolved policy can become active.

The model has no shell, network, merge, release, lifecycle, or policy-activation
tool. Full harness configurations default to `untrusted-contained`: model writes
require an explicit positive allowlist and direct/queued verification runs in
Docker. `trusted-in-process` and the current benchmark/evolution compatibility
path execute model-edited code with the operator account and must be isolated
externally when that code is untrusted.

`DockerVerifierTransport` provides that external boundary with no network, a
read-only root filesystem, dropped capabilities, bounded memory/CPU/processes,
and a read-only per-run bundle view containing only the request's digest-bound
archive and reference. The host copies that pair with no-symlink, stable-stat,
size, digest, and strict-reference validation, so the container cannot enumerate
other workspace bundles. Each run receives a new empty writable staging directory
instead of the authoritative evidence root. Docker stdout and stderr are captured
under one byte limit; overflow or timeout kills the client process group and
force-removes the recorded container ID. The host validates the receipt bytes,
digest, request, bundle, and profile bindings before atomically publishing the run
under the authoritative artifact root. The executable `/work` tmpfs is separate
from the non-executable `/tmp` tmpfs so repository-owned test programs can run
without making general temporary storage executable.

This is host containment for a single supervised run, not a hidden-oracle or
multi-tenant confidentiality boundary. The verifier service and commands still
share one container UID and mount namespace, so a command may inspect its own
request/profile and writable staging paths. Secret verifier assets require a
separate command sandbox or verifier-only mount namespace.

The Compose entry point does not prepare the per-run view for you. Set
`SISYPHUS_BUNDLE_STORE` to a new directory containing only the exact validated
request `.tar`/`.json` pair, and set `SISYPHUS_VERIFICATION_STAGING` to a new empty
directory for each invocation. Do not point either mount at the full bundle CAS,
an authoritative evidence root, or a previously published directory.

## Install

Python 3.11 or newer is required.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e .
```

Install the evolution engine only on the machine that runs offline evolution:

```bash
.venv/bin/python -m pip install -e '.[evolution]'
```

Copy `sisyphus-harness.example.toml` to `sisyphus-harness.toml` and set the
provider URL, model name, limits, cadence, verification commands, and the
smallest source roots the task may modify:

```toml
[execution]
trust_mode = "untrusted-contained"
writable_paths = ["src"]
verifier_image = "sisyphus-harness-verifier:local"
```

Build that image from `docker/verifier.Dockerfile` before the default direct or
queued run. Host verification is a compatibility escape hatch and requires an
explicit `trust_mode = "trusted-in-process"` declaration.

## Local Model

Any OpenAI-compatible chat endpoint can be used. For a Qwen3 GGUF with
`llama-server`, either disable reasoning or expose it separately from the final
content. The following bounded-reasoning configuration is the one used for the
committed historical Qwen3 30.5B evidence. That bundle targets revision
`47539e0d69a70256fcb0f0bb6b96176b67dfa99d`; it is not evidence for current
`main` behavior:

```bash
llama-server \
  --model /path/to/Qwen3-30B-A3B-Q4_K_M.gguf \
  --alias Qwen/Qwen3-30B-A3B-GGUF \
  --host 127.0.0.1 \
  --port 8081 \
  --ctx-size 16384 \
  --flash-attn on \
  --reasoning on \
  --reasoning-budget 256 \
  --reasoning-format deepseek \
  --parallel 1 \
  --no-ui
```

Use `http://127.0.0.1:8081/v1` as `provider.base_url`.

## Run

Initialize repository-local authority state:

```bash
sisyphus-harness init --repo .
```

Run one supervised coding task directly:

```bash
sisyphus-harness agent-run \
  --repo . \
  --task "Fix parse_port without changing its public signature." \
  --criterion "valid ports are accepted" \
  --criterion "invalid ports raise ValueError" \
  --run-id parse-port-baseline
```

Queue the same kind of work and let a leased worker execute it:

```bash
sisyphus-harness task-submit \
  --repo . \
  --task "Fix parse_port." \
  --criterion "valid ports are accepted" \
  --criterion "invalid ports raise ValueError" \
  --idempotency-key parse-port-v1

sisyphus-harness worker-once \
  --repo . \
  --worker-id local-worker-1
```

After argument parsing, runtime commands print structured JSON. Standard
`argparse` help and usage errors remain plain text. A failed agent or benchmark
exits non-zero.

Every task acceptance criterion must be covered verbatim by at least one
configured verification command criterion. This binds the model-facing task to
the operator-controlled receipt instead of allowing an unrelated verifier to
grant success.

That string coverage remains a compatibility check, not completion authority.
`ControlEvidenceContractService` turns only digest-bound command facts into
`EvidenceObservation` values and evaluates an operator-owned `EvidenceContract`.
It does not use `AgentResult.success` as evidence and does not mutate task or queue
state.

Initialize and inspect the derived knowledge graph with:

```bash
sisyphus-harness graph-init --repo .
sisyphus-harness graph-put-node --repo . --node-json '{...}'
sisyphus-harness graph-put-edge --repo . --edge-json '{...}'
sisyphus-harness graph-search --repo . --anchor-id document:1 --query "token rotation"
sisyphus-harness graph-dependencies --repo . --task-id task:rotate
sisyphus-harness graph-next --repo . --anchor-id document:1 \
  --query "token rotation" --max-depth 3 --dependency-max-depth 3
```

Graph results are explicitly marked `derived_candidate_only`. They support
retrieval and planning but cannot admit claims, close gaps, or execute tasks.
`graph-next` records independent candidate and dependency depth budgets, and
all returned paths, scores, eligibility reasons, and index revisions are
cross-validated before serialization.

## Benchmark And Evolve

The included benchmark keeps verifier programs outside each copied agent
workspace. Run the configured seed policy:

```bash
sisyphus-harness benchmark-run \
  --repo . \
  --dataset benchmarks/holdout.json
```

Run offline evolution with independent training and holdout sets:

```bash
sisyphus-harness evolve \
  --repo . \
  --train-dataset benchmarks/train.json \
  --holdout-dataset benchmarks/holdout.json \
  --evolution-id local-qwen30b-001
```

GEPA receives full rollout diagnostics as actionable side information. It may
change only:

- `strategy_prompt`;
- `cadence_policy`, within hard-coded numeric bounds.

The candidate is independently rerun on train and holdout data after GEPA
returns. Score deltas, hard gates, and holdout success determine whether the
candidate is proposed or rejected.

An accepted candidate is still inactive. Approval and activation are separate
operator commands:

```bash
sisyphus-harness policy-approve \
  --repo . \
  --evolution-id local-qwen30b-001 \
  --note "Reviewed holdout evidence"

sisyphus-harness policy-activate \
  --repo . \
  --evolution-id local-qwen30b-001 \
  --approval /absolute/path/from/the/approval/command.json
```

Use `--policy active` on a later agent, queue, or benchmark run.

## Historical Qwen3 30.5B Run

The committed run is historical evidence for revision
`47539e0d69a70256fcb0f0bb6b96176b67dfa99d`, not a current-release benchmark.
It used candidate
`sha256:8073fc78157f74fb15a63fc668b52e96b5540c953ef7cb9221c291c881710027`.
Against the frozen baselines it produced:

| Split | n | Baseline mean | Final-code mean | Delta | Success |
| --- | ---: | ---: | ---: | ---: | ---: |
| Train | 4 | 0.686497 | 0.897319 | +0.210822 | 0.75 |
| Frozen holdout-v3 | 2 | 0.718388 | 0.939145 | +0.220757 | 1.00 |

All hard gates passed. Fresh direct and queued coding tasks both changed only
the expected file, completed in 3 steps, compacted once, and passed the final
operator verification. The redacted receipts and per-case metrics are in
[`evidence/qwen30b-final`](evidence/qwen30b-final/README.md).

## Evidence

Authority and evidence are stored under:

```text
$(git rev-parse --git-common-dir)/sisyphus-harness/
  authority.sqlite3
  knowledge-index.sqlite3
  artifacts/
    agent/
    verification/
    evolution/
  policies/
```

This location is shared by linked worktrees and is not part of the model's
workspace. Agent steps include prompts, raw model responses, decisions, tool
outcomes, token counts, and before/after workspace hashes. Verification receipts
include command argv, criteria, full output paths, executable hashes, commit
SHA, duration, exit status, timeout state, and mutation checks.

## Development

```bash
python -m unittest discover -s tests -t .
coverage run --branch -m unittest discover -s tests -t .
coverage report -m
```

The configured branch-coverage floor is 90%.

See the [Documentation Map](docs/README.md), [Architecture](docs/architecture.md),
[Architecture and Data Pipeline](docs/architecture-and-data-pipeline.md),
[Evolution](docs/evolution.md), and [Security Policy](SECURITY.md) for the trust
model, data lineage, and operating constraints.
