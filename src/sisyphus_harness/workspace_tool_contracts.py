from __future__ import annotations

from dataclasses import dataclass

from .contracts.codec import WireModel


class ToolError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ToolOutcome(WireModel):
    tool: str
    output: dict[str, object]
    mutated: bool
