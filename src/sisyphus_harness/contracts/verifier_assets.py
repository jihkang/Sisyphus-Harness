from __future__ import annotations

from dataclasses import dataclass
import re

from .codec import WireModel, sha256_digest, strict_object


_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_BUNDLE_ID = re.compile(r"verifier-assets:sha256:[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class VerifierAssetBundleRef(WireModel):
    """Content identity for operator-owned verifier files."""

    bundle_id: str
    manifest_sha256: str
    tree_hash: str
    total_size_bytes: int
    entry_count: int
    schema_version: str = "sisyphus_harness.verifier_asset_bundle_ref.v1"

    def __post_init__(self) -> None:
        if _BUNDLE_ID.fullmatch(self.bundle_id) is None:
            raise ValueError("verifier asset bundle ID is invalid")
        for value, label in (
            (self.manifest_sha256, "verifier asset manifest digest"),
            (self.tree_hash, "verifier asset tree hash"),
        ):
            if _SHA256.fullmatch(value) is None:
                raise ValueError(f"{label} must be SHA-256")
        if self.bundle_id != f"verifier-assets:{self.manifest_sha256}":
            raise ValueError("verifier asset bundle ID and manifest digest differ")
        for value, label in (
            (self.total_size_bytes, "verifier asset total size"),
            (self.entry_count, "verifier asset entry count"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")
        if self.entry_count == 0:
            raise ValueError("verifier asset bundle must contain at least one file")
        if self.schema_version != "sisyphus_harness.verifier_asset_bundle_ref.v1":
            raise ValueError("unsupported verifier asset bundle reference schema")

    @property
    def reference_digest(self) -> str:
        return sha256_digest(WireModel.to_dict(self))

    def to_dict(self) -> dict[str, object]:
        payload = WireModel.to_dict(self)
        payload["reference_digest"] = self.reference_digest
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> VerifierAssetBundleRef:
        raw = strict_object(
            raw,
            required={
                "bundle_id",
                "manifest_sha256",
                "tree_hash",
                "total_size_bytes",
                "entry_count",
                "schema_version",
                "reference_digest",
            },
            label="verifier asset bundle reference",
        )
        total_size = _integer(raw["total_size_bytes"], "verifier asset total size")
        entry_count = _integer(raw["entry_count"], "verifier asset entry count")
        reference = cls(
            bundle_id=_string(raw["bundle_id"], "verifier asset bundle ID"),
            manifest_sha256=_string(
                raw["manifest_sha256"],
                "verifier asset manifest digest",
            ),
            tree_hash=_string(raw["tree_hash"], "verifier asset tree hash"),
            total_size_bytes=total_size,
            entry_count=entry_count,
            schema_version=_string(
                raw["schema_version"],
                "verifier asset bundle reference schema",
            ),
        )
        recorded = _string(raw["reference_digest"], "verifier asset reference digest")
        if _SHA256.fullmatch(recorded) is None:
            raise ValueError("verifier asset reference digest must be SHA-256")
        if recorded != reference.reference_digest:
            raise ValueError("verifier asset reference digest does not match content")
        return reference


def _string(raw: object, label: str) -> str:
    if not isinstance(raw, str) or not raw or "\0" in raw:
        raise ValueError(f"{label} must be a non-empty string")
    return raw


def _integer(raw: object, label: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return raw


__all__ = ["VerifierAssetBundleRef"]
