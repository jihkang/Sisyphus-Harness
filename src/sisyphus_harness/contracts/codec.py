from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
import hashlib
import json
import math
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
    if isinstance(value, float) and not math.isfinite(value):
        raise TypeError("wire floats must be finite")
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


def canonical_json_bytes(value: object) -> bytes:
    """Render a wire value deterministically for content digests."""

    return json.dumps(
        to_wire(value),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def loads_strict_json(content: str | bytes, *, label: str = "JSON") -> object:
    """Parse JSON while rejecting duplicate keys and non-standard numbers."""

    def object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate field: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise ValueError(f"{label} contains non-finite number: {value}")

    try:
        return json.loads(
            content,
            object_pairs_hook=object_from_pairs,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{label} is invalid JSON") from exc


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
