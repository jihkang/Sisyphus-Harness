from __future__ import annotations

from dataclasses import dataclass
import re

from .artifacts import ArtifactRef
from .codec import WireModel, sha256_digest, strict_object
from .verification import CommandSpec, VerificationReceipt
from .verifier_assets import VerifierAssetBundleRef
from .workspace import WorkspaceBundleRef


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class VerifierExecutionIdentity(WireModel):
    runtime: str
    image_reference: str
    image_id: str
    schema_version: str = "sisyphus_harness.verifier_execution_identity.v1"

    def __post_init__(self) -> None:
        if self.runtime != "docker":
            raise ValueError("verifier execution runtime must be docker")
        _image_reference(self.image_reference)
        _digest(self.image_id, "verifier image ID")
        if self.schema_version != "sisyphus_harness.verifier_execution_identity.v1":
            raise ValueError("unsupported verifier execution identity schema")

    @property
    def identity_digest(self) -> str:
        return sha256_digest(WireModel.to_dict(self))

    def to_dict(self) -> dict[str, object]:
        payload = WireModel.to_dict(self)
        payload["identity_digest"] = self.identity_digest
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> VerifierExecutionIdentity:
        raw = strict_object(
            raw,
            required={
                "runtime",
                "image_reference",
                "image_id",
                "schema_version",
                "identity_digest",
            },
            label="verifier execution identity",
        )
        identity = cls(
            runtime=_string(raw["runtime"], "verifier execution runtime"),
            image_reference=_string(
                raw["image_reference"],
                "verifier image reference",
            ),
            image_id=_digest(raw["image_id"], "verifier image ID"),
            schema_version=_string(
                raw["schema_version"],
                "verifier execution identity schema",
            ),
        )
        recorded = _digest(raw["identity_digest"], "verifier identity digest")
        if recorded != identity.identity_digest:
            raise ValueError("verifier identity digest does not match content")
        return identity


@dataclass(frozen=True, slots=True)
class VerificationProfile(WireModel):
    profile_id: str
    commands: tuple[CommandSpec, ...]
    asset_bundle: VerifierAssetBundleRef | None = None
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
        if self.schema_version not in {
            "sisyphus_harness.verification_profile.v1",
            "sisyphus_harness.verification_profile.v2",
        }:
            raise ValueError("unsupported verification profile schema")
        if self.schema_version.endswith(".v1"):
            if self.asset_bundle is not None:
                raise ValueError("v1 verification profile cannot bind verifier assets")
        elif self.asset_bundle is not None and type(self.asset_bundle) is not VerifierAssetBundleRef:
            raise TypeError(
                "verification profile asset bundle must be an exact reference"
            )

    def content_payload(self) -> dict[str, object]:
        payload = WireModel.to_dict(self)
        if self.schema_version.endswith(".v1"):
            payload.pop("asset_bundle")
        return payload

    @property
    def profile_digest(self) -> str:
        return sha256_digest(self.content_payload())

    def to_dict(self) -> dict[str, object]:
        payload = self.content_payload()
        payload["profile_digest"] = self.profile_digest
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> VerificationProfile:
        if not isinstance(raw, dict):
            raise ValueError("verification profile must be an object")
        schema = raw.get("schema_version")
        if schema == "sisyphus_harness.verification_profile.v1":
            required = {
                "profile_id",
                "commands",
                "schema_version",
                "profile_digest",
            }
        elif schema == "sisyphus_harness.verification_profile.v2":
            required = {
                "profile_id",
                "commands",
                "asset_bundle",
                "schema_version",
                "profile_digest",
            }
        else:
            raise ValueError("unsupported verification profile schema")
        raw = strict_object(
            raw,
            required=required,
            label="verification profile",
        )
        commands = raw["commands"]
        if not isinstance(commands, list):
            raise ValueError("verification profile commands must be a list")
        profile = cls(
            profile_id=_string(raw["profile_id"], "verification profile ID"),
            commands=tuple(CommandSpec.from_dict(item) for item in commands),
            asset_bundle=(
                VerifierAssetBundleRef.from_dict(raw["asset_bundle"])
                if raw.get("asset_bundle") is not None
                else None
            ),
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
    execution_identity: VerifierExecutionIdentity | None = None
    schema_version: str = "sisyphus_harness.bundle_verification_request.v1"

    def __post_init__(self) -> None:
        _validate_id(self.run_id, "bundle verification run ID")
        if type(self.workspace_bundle) is not WorkspaceBundleRef:
            raise ValueError("bundle verification workspace bundle is invalid")
        if type(self.profile) is not VerificationProfile:
            raise ValueError("bundle verification profile is invalid")
        if self.schema_version not in {
            "sisyphus_harness.bundle_verification_request.v1",
            "sisyphus_harness.bundle_verification_request.v2",
        }:
            raise ValueError("unsupported bundle verification request schema")
        if self.schema_version.endswith(".v1"):
            if self.execution_identity is not None:
                raise ValueError("v1 bundle request cannot bind execution identity")
            if self.profile.schema_version != "sisyphus_harness.verification_profile.v1":
                raise ValueError("v1 bundle request requires a v1 profile")
        else:
            if type(self.execution_identity) is not VerifierExecutionIdentity:
                raise TypeError("v2 bundle request requires exact execution identity")
            if self.profile.schema_version != "sisyphus_harness.verification_profile.v2":
                raise ValueError("v2 bundle request requires a v2 profile")

    def content_payload(self) -> dict[str, object]:
        payload = WireModel.to_dict(self)
        if self.schema_version.endswith(".v1"):
            payload.pop("execution_identity")
        return payload

    @property
    def request_digest(self) -> str:
        return sha256_digest(self.content_payload())

    def to_dict(self) -> dict[str, object]:
        payload = self.content_payload()
        payload["request_digest"] = self.request_digest
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> BundleVerificationRequest:
        if not isinstance(raw, dict):
            raise ValueError("bundle verification request must be an object")
        schema = raw.get("schema_version")
        if schema == "sisyphus_harness.bundle_verification_request.v1":
            required = {
                "run_id",
                "workspace_bundle",
                "profile",
                "schema_version",
                "request_digest",
            }
        elif schema == "sisyphus_harness.bundle_verification_request.v2":
            required = {
                "run_id",
                "workspace_bundle",
                "profile",
                "execution_identity",
                "schema_version",
                "request_digest",
            }
        else:
            raise ValueError("unsupported bundle verification request schema")
        raw = strict_object(
            raw,
            required=required,
            label="bundle verification request",
        )
        request = cls(
            run_id=_string(raw["run_id"], "bundle verification run ID"),
            workspace_bundle=WorkspaceBundleRef.from_dict(raw["workspace_bundle"]),
            profile=VerificationProfile.from_dict(raw["profile"]),
            execution_identity=(
                VerifierExecutionIdentity.from_dict(raw["execution_identity"])
                if raw.get("execution_identity") is not None
                else None
            ),
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
    execution_identity: VerifierExecutionIdentity | None = None
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
        if self.schema_version not in {
            "sisyphus_harness.verification_service_result.v1",
            "sisyphus_harness.verification_service_result.v2",
        }:
            raise ValueError("unsupported verification service result schema")
        if self.schema_version.endswith(".v1"):
            if self.execution_identity is not None:
                raise ValueError("v1 service result cannot bind execution identity")
        else:
            if type(self.execution_identity) is not VerifierExecutionIdentity:
                raise TypeError("v2 service result requires exact execution identity")
            if self.receipt.schema_version != "sisyphus_harness.verification.v3":
                raise ValueError("v2 service result requires a v3 verification receipt")
            if (
                self.receipt.workspace_bundle_id != self.workspace_bundle_id
                or self.receipt.profile_digest != self.profile_digest
                or self.receipt.execution_identity_digest
                != self.execution_identity.identity_digest
            ):
                raise ValueError(
                    "v2 service result and receipt bindings are inconsistent"
                )

    def content_payload(self) -> dict[str, object]:
        payload = WireModel.to_dict(self)
        if self.schema_version.endswith(".v1"):
            payload.pop("execution_identity")
        return payload

    def to_dict(self) -> dict[str, object]:
        return self.content_payload()

    @classmethod
    def from_dict(cls, raw: object) -> VerificationServiceResult:
        if not isinstance(raw, dict):
            raise ValueError("verification service result must be an object")
        schema = raw.get("schema_version")
        if schema == "sisyphus_harness.verification_service_result.v1":
            required = {
                "request_digest",
                "workspace_bundle_id",
                "profile_digest",
                "receipt",
                "receipt_artifact",
                "schema_version",
            }
        elif schema == "sisyphus_harness.verification_service_result.v2":
            required = {
                "request_digest",
                "workspace_bundle_id",
                "profile_digest",
                "receipt",
                "receipt_artifact",
                "execution_identity",
                "schema_version",
            }
        else:
            raise ValueError("unsupported verification service result schema")
        raw = strict_object(
            raw,
            required=required,
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
            execution_identity=(
                VerifierExecutionIdentity.from_dict(raw["execution_identity"])
                if raw.get("execution_identity") is not None
                else None
            ),
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


def _image_reference(raw: object) -> str:
    value = _string(raw, "verifier image reference")
    if (
        len(value) > 512
        or value.startswith("-")
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise ValueError("verifier image reference is unsafe")
    return value


__all__ = [
    "BundleVerificationRequest",
    "VerificationProfile",
    "VerificationServiceResult",
    "VerifierExecutionIdentity",
]
