from __future__ import annotations

from pathlib import Path
import re

from .receipts import write_json_atomic
from .workspace import contained_path


class RunStoreError(RuntimeError):
    pass


class AgentRunStore:
    def __init__(self, artifact_root: Path, run_id: str) -> None:
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", run_id) is None
            or run_id in {".", ".."}
        ):
            raise RunStoreError("agent run ID contains unsafe characters")
        artifact_root.mkdir(parents=True, exist_ok=True)
        self.root = contained_path(artifact_root, run_id, require_relative=True)
        if self.root.exists():
            raise RunStoreError(f"agent run already exists: {run_id}")
        self.root.mkdir(parents=True)

    def write_metadata(self, payload: dict[str, object]) -> None:
        write_json_atomic(self.root / "metadata.json", payload)

    def write_step(self, step: int, payload: dict[str, object]) -> None:
        write_json_atomic(self.root / "steps" / f"{step:04d}.json", payload)

    def write_compaction(self, index: int, payload: dict[str, object]) -> None:
        write_json_atomic(
            self.root / "compactions" / f"{index:04d}.json",
            payload,
        )

    def write_final(self, payload: dict[str, object]) -> None:
        write_json_atomic(self.root / "result.json", payload)
