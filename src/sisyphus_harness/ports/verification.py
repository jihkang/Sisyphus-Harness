from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..contracts.verification import CommandSpec, VerificationReceipt


class VerificationPort(Protocol):
    def verify(
        self,
        workspace: Path,
        commands: tuple[CommandSpec, ...],
        *,
        run_id: str | None = None,
    ) -> VerificationReceipt:
        ...
