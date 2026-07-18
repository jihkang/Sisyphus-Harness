from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re

from .codec import WireModel, strict_object


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot(WireModel):
    commit_sha: str
    state_hash: str
    changed_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkspaceBundleRef(WireModel):
    bundle_id: str
    archive_sha256: str
    size_bytes: int
    source_commit_sha: str
    source_state_hash: str
    tree_hash: str
    changed_paths: tuple[str, ...]
    entry_count: int
    schema_version: str = "sisyphus_harness.workspace_bundle_ref.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "sisyphus_harness.workspace_bundle_ref.v1":
            raise ValueError("unsupported workspace bundle reference schema")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", self.archive_sha256) is None:
            raise ValueError("workspace bundle archive digest must be SHA-256")
        if self.bundle_id != f"workspace:{self.archive_sha256}":
            raise ValueError("workspace bundle ID must match its archive digest")
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes <= 0
        ):
            raise ValueError("workspace bundle size must be positive")
        if (
            isinstance(self.entry_count, bool)
            or not isinstance(self.entry_count, int)
            or self.entry_count < 0
        ):
            raise ValueError("workspace bundle entry count must be non-negative")
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", self.source_commit_sha) is None:
            raise ValueError("workspace bundle source commit must be a full SHA")
        for label, digest in (
            ("source state", self.source_state_hash),
            ("tree", self.tree_hash),
        ):
            if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
                raise ValueError(f"workspace bundle {label} digest must be SHA-256")
        normalized = tuple(sorted(_validate_relative_paths(self.changed_paths)))
        if len(set(normalized)) != len(normalized):
            raise ValueError("workspace bundle changed paths must be unique")
        object.__setattr__(self, "changed_paths", normalized)

    @classmethod
    def from_dict(cls, raw: object) -> WorkspaceBundleRef:
        required = {
            "schema_version",
            "bundle_id",
            "archive_sha256",
            "size_bytes",
            "source_commit_sha",
            "source_state_hash",
            "tree_hash",
            "changed_paths",
            "entry_count",
        }
        raw = strict_object(
            raw,
            required=required,
            label="workspace bundle reference",
        )
        strings = {
            key: raw[key]
            for key in (
                "schema_version",
                "bundle_id",
                "archive_sha256",
                "source_commit_sha",
                "source_state_hash",
                "tree_hash",
            )
        }
        if any(not isinstance(value, str) for value in strings.values()):
            raise ValueError("workspace bundle reference string fields are invalid")
        changed_paths = raw["changed_paths"]
        if not isinstance(changed_paths, list) or any(
            not isinstance(path, str) for path in changed_paths
        ):
            raise ValueError("workspace bundle changed paths must be a string list")
        size_bytes = raw["size_bytes"]
        entry_count = raw["entry_count"]
        if (
            isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or isinstance(entry_count, bool)
            or not isinstance(entry_count, int)
        ):
            raise ValueError("workspace bundle numeric fields must be integers")
        return cls(
            bundle_id=strings["bundle_id"],
            archive_sha256=strings["archive_sha256"],
            size_bytes=size_bytes,
            source_commit_sha=strings["source_commit_sha"],
            source_state_hash=strings["source_state_hash"],
            tree_hash=strings["tree_hash"],
            changed_paths=tuple(changed_paths),
            entry_count=entry_count,
            schema_version=strings["schema_version"],
        )


def _validate_relative_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for path in paths:
        candidate = PurePosixPath(path)
        if (
            not path
            or "\\" in path
            or "\0" in path
            or candidate.is_absolute()
            or candidate.as_posix() != path
            or any(part in {"", ".", ".."} for part in candidate.parts)
        ):
            raise ValueError(f"workspace bundle path is unsafe: {path!r}")
        normalized.append(path)
    return tuple(normalized)
