from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import time
import uuid

from ..contracts.artifacts import ArtifactRef
from ..contracts.verification import CommandSpec, VerificationReceipt
from ..contracts.verification_service import (
    BundleVerificationRequest,
    VerificationProfile,
)
from ..infra.workspace_bundle import FilesystemWorkspaceBundleStore
from ..ports.verification_service import (
    TimeoutBoundVerificationServicePort,
    VerificationServicePort,
)
from .receipt_observations import validate_final_verification_bindings


@dataclass(slots=True)
class BundleVerificationAdapter:
    """Adapt immutable bundle verification to the Agent verification port."""

    bundle_store: FilesystemWorkspaceBundleStore
    verifier: VerificationServicePort
    _references: dict[str, ArtifactRef] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def verify(
        self,
        workspace: Path,
        commands: tuple[CommandSpec, ...],
        *,
        run_id: str | None = None,
        request_digest: str | None = None,
        deadline_monotonic: float | None = None,
    ) -> VerificationReceipt:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            raise TimeoutError("global verification deadline exceeded")
        resolved_run_id = run_id or f"verification-{uuid.uuid4().hex}"
        profile = VerificationProfile(
            profile_id=_profile_id(commands),
            commands=commands,
        )
        request = BundleVerificationRequest(
            run_id=resolved_run_id,
            workspace_bundle=self.bundle_store.create(workspace),
            profile=profile,
        )
        if request_digest is not None and request_digest != request.request_digest:
            raise ValueError("verification request digest does not match bundle request")
        if deadline_monotonic is not None and isinstance(
            self.verifier,
            TimeoutBoundVerificationServicePort,
        ):
            remaining = deadline_monotonic - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("global verification deadline exceeded")
            result = self.verifier.execute_with_timeout(
                request,
                timeout_seconds=remaining,
            )
        else:
            result = self.verifier.execute(request)
        validate_final_verification_bindings(request, result)
        receipt = self.verifier.read_receipt(result.receipt_artifact)
        if receipt != result.receipt:
            raise ValueError("verification result does not match authoritative receipt")
        self._references[resolved_run_id] = result.receipt_artifact
        return receipt

    def receipt_reference(self, run_id: str) -> ArtifactRef:
        try:
            return self._references[run_id]
        except KeyError as exc:
            raise FileNotFoundError(f"verification receipt not recorded: {run_id}") from exc

    def read_receipt(self, reference: ArtifactRef) -> VerificationReceipt:
        return self.verifier.read_receipt(reference)


def _profile_id(commands: tuple[CommandSpec, ...]) -> str:
    payload = json.dumps(
        [command.to_dict() for command in commands],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"agent-{hashlib.sha256(payload).hexdigest()[:24]}"


__all__ = ["BundleVerificationAdapter"]
