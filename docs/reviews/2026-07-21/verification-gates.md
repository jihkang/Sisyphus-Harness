# Verification And Release Gates

## Finding Closure

| Finding | Required proof |
| --- | --- |
| SH-P0-001 | oracle mutation attempt is rejected while an allowed source repair succeeds |
| SH-P0-002 | Worker cannot publish semantic success; stale attempt/outcome transaction fails |
| SH-P0-003 | manifest reports `historical` and `source_matches_head=false`; README includes revision and n |
| SH-P0-004 | CODEOWNERS, required current checks, approval and branch protection are active |
| SH-P0-005 | root-object strict schema contract and a real supported endpoint accept the request |
| SH-P0-006 | default composition is Docker bundle verification; external write/network probes fail |

## Required Commands

```text
uv run --frozen --group dev coverage run --branch -m unittest discover -s tests -t .
uv run --frozen --group dev coverage report -m
uvx --from ruff==0.15.22 ruff check .
uvx --from bandit==1.9.4 bandit -r src -ll
python evidence/qwen30b-final/verify_manifest.py
uv lock --check
python -m compileall -q src tests benchmarks
uv build --offline
```

Container inspection must additionally prove non-root UID, no network, read-only
root, dropped capabilities, bounded PID/memory/CPU/output, exact bundle view, and
fresh non-authoritative staging. Release closure also requires an isolated wheel
install and current-head CI/PR/merge evidence.

The real-container regression is opt-in locally and mandatory in CI:

```text
SISYPHUS_DOCKER_INTEGRATION=1 \
SISYPHUS_VERIFIER_IMAGE=sisyphus-harness-verifier:local \
uv run --frozen --group dev python -m unittest tests.test_docker_verifier_integration -v
```
