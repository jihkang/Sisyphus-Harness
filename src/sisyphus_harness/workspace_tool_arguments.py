from __future__ import annotations

from pathlib import Path
from typing import Any

from .workspace_tool_contracts import ToolError


def reject_unknown(arguments: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(arguments).difference(allowed))
    if unknown:
        raise ToolError(f"tool arguments contain unknown fields: {', '.join(unknown)}")


def normalize_scope_path(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return ""
    normalized = Path(stripped).as_posix().rstrip("/")
    if normalized in {"", "."}:
        return ""
    return normalized


def required_string(
    arguments: dict[str, Any],
    field: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or (not allow_empty and not value):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise ToolError(f"{field} must be {qualifier}")
    return value


def required_string_list(
    arguments: dict[str, Any],
    field: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    value = arguments.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ToolError(f"{field} must be an array of strings")
    if not allow_empty and not value:
        raise ToolError(f"{field} must be a non-empty array of strings")
    lines: list[str] = []
    for item in value:
        lines.extend(item.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    return lines


def text_or_lines(
    arguments: dict[str, Any],
    *,
    text_field: str,
    lines_field: str,
    allow_empty: bool,
) -> tuple[str, str]:
    has_text = text_field in arguments
    has_lines = lines_field in arguments
    if has_text == has_lines:
        raise ToolError(
            f"exactly one of {text_field} or {lines_field} must be provided"
        )
    if has_lines:
        lines = required_string_list(
            arguments,
            lines_field,
            allow_empty=allow_empty,
        )
        return "\n".join(lines), "lines"
    return required_string(
        arguments,
        text_field,
        allow_empty=allow_empty,
    ), "text"


def optional_positive_int(
    value: object,
    field: str,
    default: int | None,
) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ToolError(f"{field} must be a positive integer")
    return value
