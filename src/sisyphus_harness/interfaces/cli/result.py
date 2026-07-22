from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CliResult:
    payload: object
    exit_code: int = 0
