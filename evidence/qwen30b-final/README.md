# Historical Qwen3 30.5B Evidence

This bundle is historical evidence bound to revision
`47539e0d69a70256fcb0f0bb6b96176b67dfa99d`. It does not establish performance
or security properties for current `main`. It records the accepted local-model evolution and the subsequent
active-policy execution proofs for Sisyphus Harness. Paths identifying a local
user or machine have been replaced with symbolic roots. The model file, HMAC
authority key, hidden verifier source, and verifier stdout/stderr are not
included.

## Runtime

- Model: `Qwen/Qwen3-30B-A3B-GGUF`
- Quantization: `Qwen3-30B-A3B-Q4_K_M.gguf`
- Model SHA-256:
  `0d003f6662faee786ed5da3e31b29c978de5ae5d275c8794c606a7f3c01aa8f5`
- llama.cpp: build `9290`, commit `bcfd1989e`
- Context: 16,384 tokens, one parallel slot, reasoning enabled with a
  256-token budget
- GEPA: `gepa==0.1.4`, seed 7, at most 60 metric calls and 6 proposals

The full redacted runtime argv and harness settings are in
`runtime-provenance.json`.

## Evolution Result

Evolution `qwen30b-gepa-20260718-11` accepted candidate
`sha256:8073fc78157f74fb15a63fc668b52e96b5540c953ef7cb9221c291c881710027`.

| Split | n | Baseline mean | Candidate mean | Delta | Baseline success | Candidate success |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Train | 4 | 0.686497 | 0.917105 | +0.230609 | 0.50 | 0.75 |
| Frozen holdout-v3 | 2 | 0.718388 | 0.952303 | +0.233914 | 0.50 | 1.00 |

All train and holdout hard gates passed. The candidate passed every holdout
case. On train, page-size reached 100 percent criterion correctness but did not
emit its final `finish` decision before the 20-step budget, which accounts for
the 0.75 task success rate. The promotion contract requires measurable train
and holdout score deltas, non-regressing holdout success, every holdout task to
pass, and all hard gates; it does not require every train task to finish.

Holdout-v1 and holdout-v2 were spent during rejected development runs. V3 was
hashed before its first model evaluation and used once for the final
baseline/candidate comparison. Its lock is `benchmarks/holdout-v3.lock.json`.

## Approval And Revalidation

The operator approval and active-policy pointer were HMAC-SHA256 verified before
export. Their original SHA-256 values and signatures are retained in redacted
summaries; the secret authority key is not exported.

Active-policy revalidation exactly reproduced the accepted result:

- Train mean `0.917105`, success `0.75`, all hard gates passed.
- Holdout mean `0.952303`, success `1.00`, all hard gates passed.

After the pre-publication code review, the unchanged active candidate was run
again against the reviewed implementation. The new train mean was `0.897319`
(`+0.210822` over baseline) with `0.75` success, and frozen holdout-v3 was
`0.939145` (`+0.220757`) with `1.00` success. All hard gates passed. These
nondeterministic but still materially improved results, plus fresh direct and
queue execution proofs, are under `artifacts/final-code-review`. The original
GEPA result remains the authority for candidate selection.

## Real Coding Proofs

The direct and queued tasks each began from a committed fixture where
`add(left, right)` subtracted. The 9,328-byte source fixture forced context
pressure without changing the task behavior. The real 30.5B model read the
file, changed subtraction to addition, compacted automatically, and passed the
operator verifier.

- Direct: 3 steps, 1 mutation, 1 compaction, 1 passing verification.
- Queue: job `job-80de51feba2d4faf847a334f4fee06b3`, one lease attempt,
  terminal `completed`, 3 steps, 1 compaction, 1 passing verification.
- Three submissions using `qwen30b-active-queue-proof-v1` returned the same job
  ID before and after completion; no duplicate job was created.

An earlier two-file direct attempt is retained under `artifacts/rejected`. It
compacted three times but failed closed at the stagnation threshold and is not
counted as successful evidence.

## Publication And Post-Merge Run

[Pull request #1](https://github.com/jihkang/Sisyphus-Harness/pull/1) passed
Python 3.11, Python 3.14, installed GEPA, package, and evidence CI before it was
squash-merged as
`47539e0d69a70256fcb0f0bb6b96176b67dfa99d`. Local `main` was then fetched and
fast-forwarded to the same commit.

A fresh Qwen3 30.5B direct smoke from that synchronized `main` completed in 3
steps with 1 automatic compaction and 1 passing verification. The merge/CI
receipt and redacted run artifacts are under `artifacts/post-merge-main`.

## Package Verification

- 139 tests passed after the code-review fixes.
- Branch coverage: 91.6 percent, above the 90 percent gate.
- The installed `gepa==0.1.4` integration test passed.
- `compileall`, lock validation, and diff validation passed.
- `uv build --offline` produced the sdist and wheel.
- A fresh venv installed the wheel with `pip --no-index --no-deps`; import and
  CLI authority initialization succeeded outside the checkout.

`manifest.json` hashes every bundled artifact and the source inputs at pinned Git
revision `47539e0d69a70256fcb0f0bb6b96176b67dfa99d`. This keeps the historical
30.5B execution evidence bound to the code that produced it as later revisions
change the runtime.
`final-verification.json` records the original final checks; the post-review
rerun is recorded separately in `final-code-verification.json`.
