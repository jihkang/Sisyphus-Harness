from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    commit_sha: str
    state_hash: str
    changed_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "commit_sha": self.commit_sha,
            "state_hash": self.state_hash,
            "changed_paths": list(self.changed_paths),
        }
