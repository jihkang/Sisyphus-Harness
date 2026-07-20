from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re

from .codec import WireModel, strict_object


_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ArtifactRef(WireModel):
    artifact_id: str
    sha256: str
    size_bytes: int
    media_type: str
    schema_version: str = "sisyphus_harness.artifact_ref.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "sisyphus_harness.artifact_ref.v1":
            raise ValueError("unsupported artifact reference schema")
        _validate_artifact_id(self.artifact_id)
        if _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("artifact digest must be SHA-256")
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError("artifact size must be a non-negative integer")
        if (
            not self.media_type
            or self.media_type.strip() != self.media_type
            or any(character.isspace() for character in self.media_type)
            or "/" not in self.media_type
        ):
            raise ValueError("artifact media type is invalid")

    @classmethod
    def from_dict(cls, raw: object) -> ArtifactRef:
        raw = strict_object(
            raw,
            required={
                "artifact_id",
                "sha256",
                "size_bytes",
                "media_type",
                "schema_version",
            },
            label="artifact reference",
        )
        string_fields = {
            key: raw[key]
            for key in ("artifact_id", "sha256", "media_type", "schema_version")
        }
        if any(not isinstance(value, str) for value in string_fields.values()):
            raise ValueError("artifact reference string fields are invalid")
        size_bytes = raw["size_bytes"]
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int):
            raise ValueError("artifact reference size must be an integer")
        return cls(
            artifact_id=string_fields["artifact_id"],
            sha256=string_fields["sha256"],
            size_bytes=size_bytes,
            media_type=string_fields["media_type"],
            schema_version=string_fields["schema_version"],
        )


def _validate_artifact_id(value: str) -> None:
    candidate = PurePosixPath(value)
    if (
        not value
        or len(value) > 512
        or "\\" in value
        or "\0" in value
        or candidate.is_absolute()
        or candidate.as_posix() != value
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ValueError("artifact ID must be a safe relative path")
