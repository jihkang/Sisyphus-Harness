from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ...contracts.codec import loads_strict_json
from ...workspace import contained_path


def repo_path(repo_root: Path, raw: str) -> Path:
    return contained_path(repo_root, raw)


def json_object(raw: str, field: str) -> dict[str, object]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"{field} must decode to an object")
    return payload


def strict_json_object(raw: str, field: str) -> dict[str, object]:
    payload = loads_strict_json(raw, label=field)
    if not isinstance(payload, dict):
        raise ValueError(f"{field} must decode to an object")
    return payload


def strict_json_file(path: Path, *, label: str) -> dict[str, object]:
    content = path.read_bytes()
    if len(content) > 4 * 1024 * 1024:
        raise ValueError(f"{label} exceeds byte limit")
    payload = loads_strict_json(content, label=label)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be an object")
    return payload


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
