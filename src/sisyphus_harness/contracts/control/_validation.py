from __future__ import annotations

import re


_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


def validate_attempt_identity(job_id: str, attempt: int, attempt_id: str) -> None:
    string(job_id, "job ID")
    positive_integer(attempt, "attempt")
    string(attempt_id, "attempt ID")
    if attempt_id != f"{job_id}/attempt-{attempt:04d}":
        raise ValueError("attempt ID is inconsistent")


def string(raw: object, label: str) -> str:
    if not isinstance(raw, str) or not raw or "\0" in raw:
        raise ValueError(f"{label} must be a non-empty string")
    return raw


def positive_integer(raw: object, label: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return raw


def validate_producer_authority(raw: object) -> str:
    value = string(raw, "task outcome producer authority")
    if (
        len(value) > 256
        or value.strip() != value
        or any(character.isspace() for character in value)
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("task outcome producer authority must be a bounded token")
    return value


def digest(raw: object, label: str) -> str:
    value = string(raw, label)
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be SHA-256")
    return value
