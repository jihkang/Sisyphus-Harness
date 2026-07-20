from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..contracts.artifacts import ArtifactRef
from ..contracts.verification import CommandSpec, VerificationReceipt


@runtime_checkable
class VerificationPort(Protocol):
    def verify(
        self,
        workspace: Path,
        commands: tuple[CommandSpec, ...],
        *,
        run_id: str | None = None,
        request_digest: str | None = None,
        deadline_monotonic: float | None = None,
    ) -> VerificationReceipt:
        ...


@runtime_checkable
class VerificationEvidencePort(Protocol):
    def receipt_reference(self, run_id: str) -> ArtifactRef:
        ...

    def read_receipt(self, reference: ArtifactRef) -> VerificationReceipt:
        ...
