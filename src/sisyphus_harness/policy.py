from __future__ import annotations

from datetime import UTC, datetime
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import secrets
from typing import Any

from .evolution import CandidatePolicy, validate_evolution_id
from .receipts import write_json_atomic
from .workspace import contained_path


class PolicyError(RuntimeError):
    pass


class PolicyRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.key_path = self.root / "authority.key"

    def approve(
        self,
        evolution_result_path: Path,
        *,
        note: str = "",
    ) -> Path:
        result = _read_json_object(evolution_result_path)
        if result.get("status") != "proposed" or result.get("accepted") is not True:
            raise PolicyError("only accepted proposed candidates can be approved")
        evolution_id = validate_evolution_id(_required_string(result, "evolution_id"))
        candidate = CandidatePolicy.from_dict(result.get("candidate"))
        payload: dict[str, object] = {
            "schema_version": "sisyphus_harness.policy_approval.v1",
            "evolution_id": evolution_id,
            "candidate_hash": candidate.candidate_hash,
            "operator": {
                "username": getpass.getuser(),
                "uid": os.getuid() if hasattr(os, "getuid") else None,
                "hostname": platform.node(),
            },
            "note": note,
            "approved_at": _utc_now(),
        }
        payload["signature"] = self._sign(payload)
        path = contained_path(
            self.root / "approvals",
            f"{evolution_id}-{candidate.candidate_hash.removeprefix('sha256:')[:16]}.json",
            require_relative=True,
        )
        if path.exists():
            raise PolicyError("candidate already has an approval receipt")
        write_json_atomic(path, payload)
        return path

    def activate(
        self,
        evolution_result_path: Path,
        approval_path: Path,
    ) -> Path:
        result = _read_json_object(evolution_result_path)
        if result.get("status") != "proposed" or result.get("accepted") is not True:
            raise PolicyError("only accepted proposed candidates can be activated")
        evolution_id = validate_evolution_id(_required_string(result, "evolution_id"))
        candidate = CandidatePolicy.from_dict(result.get("candidate"))
        approval = _read_json_object(approval_path)
        signature = approval.pop("signature", None)
        if not isinstance(signature, str) or not self._verify(approval, signature):
            raise PolicyError("approval signature is invalid")
        if approval.get("evolution_id") != evolution_id:
            raise PolicyError("approval belongs to another evolution run")
        if approval.get("candidate_hash") != candidate.candidate_hash:
            raise PolicyError("approval candidate hash does not match")
        payload: dict[str, object] = {
            "schema_version": "sisyphus_harness.active_policy.v1",
            "evolution_id": evolution_id,
            "candidate": candidate.to_dict(),
            "approval": approval,
            "activated_at": _utc_now(),
        }
        payload["signature"] = self._sign(payload)
        path = self.root / "active.json"
        write_json_atomic(path, payload)
        return path

    def load_active(self) -> CandidatePolicy | None:
        path = self.root / "active.json"
        if not path.exists():
            return None
        payload = _read_json_object(path)
        signature = payload.pop("signature", None)
        if not isinstance(signature, str) or not self._verify(payload, signature):
            raise PolicyError("active policy signature is invalid")
        return CandidatePolicy.from_dict(payload.get("candidate"))

    def _sign(self, payload: dict[str, object]) -> str:
        key = self._load_or_create_key()
        rendered = _canonical_json(payload)
        return f"hmac-sha256:{hmac.new(key, rendered, hashlib.sha256).hexdigest()}"

    def _verify(self, payload: dict[str, object], signature: str) -> bool:
        expected = self._sign(payload)
        return hmac.compare_digest(expected, signature)

    def _load_or_create_key(self) -> bytes:
        try:
            return self.key_path.read_bytes()
        except FileNotFoundError:
            key = secrets.token_bytes(32)
            descriptor = os.open(
                self.key_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                os.write(descriptor, key)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return key


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise PolicyError(f"invalid policy artifact: {path}") from exc
    if not isinstance(payload, dict):
        raise PolicyError(f"policy artifact must be an object: {path}")
    return payload


def _required_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise PolicyError(f"policy artifact {field} must be a non-empty string")
    return value


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
