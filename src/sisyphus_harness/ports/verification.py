from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..contracts.verification import CommandSpec, VerificationReceipt


@runtime_checkable
class VerificationPort(Protocol):
    def verify(
        self,
        workspace: Path,
        commands: tuple[CommandSpec, ...],
        *,
        run_id: str | None = None,
    ) -> VerificationReceipt:
        ...
