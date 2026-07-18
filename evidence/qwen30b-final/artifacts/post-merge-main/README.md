# Post-Merge Main Proof

This directory binds the merged implementation pull request and its successful
GitHub Actions run to a fresh Qwen3 30.5B execution from synchronized local
`main`.

The smoke task started from the same committed arithmetic fixture used by the
earlier direct proof. The active signed policy changed only `arithmetic.py`,
completed in 3 steps, compacted once, and passed the operator verifier.

Local absolute paths are replaced with `$POST_MERGE_MAIN_WORKSPACE`. The model,
authority key, verifier stdout/stderr, and local server log are not bundled.
