from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any


class WireModel:
    """Encode dataclass fields into JSON-compatible wire values."""

    __slots__ = ()

    def to_dict(self) -> dict[str, object]:
        if not is_dataclass(self):
            raise TypeError("WireModel must be used with a dataclass")
        return {
            field.name: to_wire(getattr(self, field.name))
            for field in fields(self)
        }


def to_wire(value: Any) -> Any:
    if isinstance(value, WireModel):
        return value.to_dict()
    if isinstance(value, Enum):
        return to_wire(value.value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        encoded: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("wire mappings require string keys")
            encoded[key] = to_wire(item)
        return encoded
    if isinstance(value, (list, tuple)):
        return [to_wire(item) for item in value]
    if is_dataclass(value):
        return {
            field.name: to_wire(getattr(value, field.name))
            for field in fields(value)
        }
    raise TypeError(f"unsupported wire value: {type(value).__name__}")


def strict_object(
    raw: object,
    *,
    required: set[str],
    optional: set[str] | None = None,
    label: str,
    error_type: type[Exception] = ValueError,
) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise error_type(f"{label} must be an object")
    allowed = required.union(optional or set())
    unknown = sorted(set(raw).difference(allowed))
    missing = sorted(required.difference(raw))
    if unknown:
        raise error_type(f"{label} contains unknown fields: {', '.join(unknown)}")
    if missing:
        raise error_type(f"{label} is missing fields: {', '.join(missing)}")
    return raw
