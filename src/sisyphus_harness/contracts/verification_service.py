from __future__ import annotations

from dataclasses import dataclass
import re

from .artifacts import ArtifactRef
from .codec import WireModel, sha256_digest, strict_object
from .verification import CommandSpec, VerificationReceipt
from .workspace import WorkspaceBundleRef


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class VerificationProfile(WireModel):
    profile_id: str
    commands: tuple[CommandSpec, ...]
    schema_version: str = "sisyphus_harness.verification_profile.v1"

    def __post_init__(self) -> None:
        _validate_id(self.profile_id, "verification profile ID")
        if type(self.commands) is not tuple:
            raise ValueError(
                "verification profile commands must be an immutable tuple"
            )
        if not self.commands or any(
            type(command) is not CommandSpec for command in self.commands
        ):
            raise ValueError(
                "verification profile requires exact CommandSpec values"
            )
        names = [command.name for command in self.commands]
        if len(set(names)) != len(names):
            raise ValueError("verification profile command names must be unique")
        if self.schema_version != "sisyphus_harness.verification_profile.v1":
            raise ValueError("unsupported verification profile schema")

    @property
    def profile_digest(self) -> str:
        return sha256_digest(WireModel.to_dict(self))

    def to_dict(self) -> dict[str, object]:
        payload = WireModel.to_dict(self)
        payload["profile_digest"] = self.profile_digest
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> VerificationProfile:
        raw = strict_object(
            raw,
            required={
                "profile_id",
                "commands",
                "schema_version",
                "profile_digest",
            },
            label="verification profile",
        )
        commands = raw["commands"]
        if not isinstance(commands, list):
            raise ValueError("verification profile commands must be a list")
        profile = cls(
            profile_id=_string(raw["profile_id"], "verification profile ID"),
            commands=tuple(CommandSpec.from_dict(item) for item in commands),
            schema_version=_string(
                raw["schema_version"],
                "verification profile schema",
            ),
        )
        recorded = _digest(raw["profile_digest"], "verification profile digest")
        if recorded != profile.profile_digest:
            raise ValueError("verification profile digest does not match content")
        return profile


@dataclass(frozen=True, slots=True)
class BundleVerificationRequest(WireModel):
    run_id: str
    workspace_bundle: WorkspaceBundleRef
    profile: VerificationProfile
    schema_version: str = "sisyphus_harness.bundle_verification_request.v1"

    def __post_init__(self) -> None:
        _validate_id(self.run_id, "bundle verification run ID")
        if type(self.workspace_bundle) is not WorkspaceBundleRef:
            raise ValueError("bundle verification workspace bundle is invalid")
        if type(self.profile) is not VerificationProfile:
            raise ValueError("bundle verification profile is invalid")
        if self.schema_version != "sisyphus_harness.bundle_verification_request.v1":
            raise ValueError("unsupported bundle verification request schema")

    @property
    def request_digest(self) -> str:
        return sha256_digest(WireModel.to_dict(self))

    def to_dict(self) -> dict[str, object]:
        payload = WireModel.to_dict(self)
        payload["request_digest"] = self.request_digest
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> BundleVerificationRequest:
        raw = strict_object(
            raw,
            required={
                "run_id",
                "workspace_bundle",
                "profile",
                "schema_version",
                "request_digest",
            },
            label="bundle verification request",
        )
        request = cls(
            run_id=_string(raw["run_id"], "bundle verification run ID"),
            workspace_bundle=WorkspaceBundleRef.from_dict(raw["workspace_bundle"]),
            profile=VerificationProfile.from_dict(raw["profile"]),
            schema_version=_string(
                raw["schema_version"],
                "bundle verification request schema",
            ),
        )
        recorded = _digest(raw["request_digest"], "bundle request digest")
        if recorded != request.request_digest:
            raise ValueError("bundle verification request digest does not match content")
        return request


@dataclass(frozen=True, slots=True)
class VerificationServiceResult(WireModel):
    request_digest: str
    workspace_bundle_id: str
    profile_digest: str
    receipt: VerificationReceipt
    receipt_artifact: ArtifactRef
    schema_version: str = "sisyphus_harness.verification_service_result.v1"

    def __post_init__(self) -> None:
        _digest(self.request_digest, "verification service request digest")
        _string(self.workspace_bundle_id, "verification service workspace bundle ID")
        _digest(self.profile_digest, "verification service profile digest")
        if type(self.receipt) is not VerificationReceipt:
            raise ValueError("verification service receipt is invalid")
        if type(self.receipt_artifact) is not ArtifactRef:
            raise ValueError("verification service receipt artifact is invalid")
        if self.receipt.request_digest != self.request_digest:
            raise ValueError("verification receipt is not bound to the service request")
        if self.receipt_artifact.artifact_id != f"{self.receipt.run_id}/receipt.json":
            raise ValueError("verification receipt artifact ID is inconsistent")
        if self.schema_version != "sisyphus_harness.verification_service_result.v1":
            raise ValueError("unsupported verification service result schema")

    @classmethod
    def from_dict(cls, raw: object) -> VerificationServiceResult:
        raw = strict_object(
            raw,
            required={
                "request_digest",
                "workspace_bundle_id",
                "profile_digest",
                "receipt",
                "receipt_artifact",
                "schema_version",
            },
            label="verification service result",
        )
        return cls(
            request_digest=_digest(
                raw["request_digest"],
                "verification service request digest",
            ),
            workspace_bundle_id=_string(
                raw["workspace_bundle_id"],
                "verification service workspace bundle ID",
            ),
            profile_digest=_digest(
                raw["profile_digest"],
                "verification service profile digest",
            ),
            receipt=VerificationReceipt.from_dict(raw["receipt"]),
            receipt_artifact=ArtifactRef.from_dict(raw["receipt_artifact"]),
            schema_version=_string(
                raw["schema_version"],
                "verification service result schema",
            ),
        )


def _validate_id(value: str, label: str) -> None:
    if _SAFE_ID.fullmatch(value) is None or value in {".", ".."}:
        raise ValueError(f"{label} contains unsafe characters")


def _string(raw: object, label: str) -> str:
    if not isinstance(raw, str) or not raw or "\0" in raw:
        raise ValueError(f"{label} must be a non-empty string")
    return raw


def _digest(raw: object, label: str) -> str:
    value = _string(raw, label)
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be SHA-256")
    return value
