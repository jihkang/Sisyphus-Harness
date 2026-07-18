from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    evidence_root = Path(__file__).resolve().parent
    repository_root = evidence_root.parents[1]
    manifest_path = evidence_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    checked = 0
    for section in ("source_inputs", "bundled_artifacts"):
        for entry in manifest[section]:
            path = repository_root / entry["path"]
            if not path.is_file():
                raise RuntimeError(f"missing manifest path: {entry['path']}")
            if path.stat().st_size != entry["size_bytes"]:
                raise RuntimeError(f"size mismatch: {entry['path']}")
            if sha256(path) != entry["sha256"]:
                raise RuntimeError(f"SHA-256 mismatch: {entry['path']}")
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

    print(json.dumps({"checked_files": checked, "status": "verified"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
