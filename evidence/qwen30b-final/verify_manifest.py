from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_revision_path(repository_root: Path, revision: str, path: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repository_root), "show", f"{revision}:{path}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"missing source input at {revision}: {path}: {detail}")
    return completed.stdout


def current_revision(repository_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    revision = completed.stdout.strip()
    if completed.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise RuntimeError("could not resolve the current Git revision")
    return revision


def main() -> int:
    evidence_root = Path(__file__).resolve().parent
    repository_root = evidence_root.parents[1]
    manifest_path = evidence_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "sisyphus_harness.evidence_manifest.v2":
        raise RuntimeError("unsupported evidence manifest schema")
    claim_scope = manifest.get("claim_scope")
    if claim_scope not in {"historical", "current_release"}:
        raise RuntimeError("claim_scope must be historical or current_release")
    source_revision = manifest.get("source_revision")
    if not isinstance(source_revision, str) or re.fullmatch(
        r"[0-9a-f]{40}", source_revision
    ) is None:
        raise RuntimeError("source_revision must be a full lowercase Git commit SHA")
    head_revision = current_revision(repository_root)
    source_matches_head = source_revision == head_revision
    if claim_scope == "current_release" and not source_matches_head:
        raise RuntimeError(
            "current_release evidence source_revision does not match current HEAD"
        )

    checked = 0
    for entry in manifest["source_inputs"]:
        content = read_revision_path(repository_root, source_revision, entry["path"])
        if len(content) != entry["size_bytes"]:
            raise RuntimeError(f"source size mismatch: {entry['path']}")
        if sha256_bytes(content) != entry["sha256"]:
            raise RuntimeError(f"source SHA-256 mismatch: {entry['path']}")
        checked += 1

    for entry in manifest["bundled_artifacts"]:
        path = repository_root / entry["path"]
        if not path.is_file():
            raise RuntimeError(f"missing bundled artifact: {entry['path']}")
        if path.stat().st_size != entry["size_bytes"]:
            raise RuntimeError(f"artifact size mismatch: {entry['path']}")
        if sha256(path) != entry["sha256"]:
            raise RuntimeError(f"artifact SHA-256 mismatch: {entry['path']}")
        checked += 1

    expected = {
        path.relative_to(repository_root).as_posix()
        for path in evidence_root.rglob("*")
        if path.is_file()
        and path != manifest_path
        and "__pycache__" not in path.parts
    }
    declared = {entry["path"] for entry in manifest["bundled_artifacts"]}
    if declared != expected:
        missing = sorted(expected - declared)
        extra = sorted(declared - expected)
        raise RuntimeError(
            f"evidence inventory mismatch; missing={missing}, extra={extra}"
        )

    print(
        json.dumps(
            {
                "checked_files": checked,
                "claim_scope": claim_scope,
                "source_matches_head": source_matches_head,
                "status": "verified",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
