from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat

from ..contracts.artifacts import ArtifactRef
from ..contracts.codec import loads_strict_json
from ..contracts.verification import VerificationReceipt


VERIFICATION_RECEIPT_MEDIA_TYPE = (
    "application/vnd.sisyphus-harness.verification-receipt+json"
)
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class VerificationEvidenceError(RuntimeError):
    pass


class FilesystemVerificationEvidenceStore:
    def __init__(self, root: Path, *, max_receipt_bytes: int = 4 * 1024 * 1024) -> None:
        if (
            isinstance(max_receipt_bytes, bool)
            or not isinstance(max_receipt_bytes, int)
            or max_receipt_bytes <= 0
        ):
            raise ValueError("verification receipt byte limit must be positive")
        self.root = root
        self.max_receipt_bytes = max_receipt_bytes

    def receipt_reference(self, run_id: str) -> ArtifactRef:
        if _SAFE_RUN_ID.fullmatch(run_id) is None or run_id in {".", ".."}:
            raise VerificationEvidenceError(
                "verification evidence run ID contains unsafe characters"
            )
        artifact_id = f"{run_id}/receipt.json"
        content = self._read_bytes(artifact_id)
        receipt = self._parse_receipt(content)
        if receipt.run_id != run_id:
            raise VerificationEvidenceError(
                "verification receipt run ID does not match its artifact path"
            )
        return ArtifactRef(
            artifact_id=artifact_id,
            sha256=f"sha256:{hashlib.sha256(content).hexdigest()}",
            size_bytes=len(content),
            media_type=VERIFICATION_RECEIPT_MEDIA_TYPE,
        )

    def read_receipt(self, reference: ArtifactRef) -> VerificationReceipt:
        if reference.media_type != VERIFICATION_RECEIPT_MEDIA_TYPE:
            raise VerificationEvidenceError(
                "artifact is not a verification receipt"
            )
        content = self._read_bytes(reference.artifact_id)
        if len(content) != reference.size_bytes:
            raise VerificationEvidenceError("verification receipt size does not match")
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if digest != reference.sha256:
            raise VerificationEvidenceError("verification receipt digest does not match")
        receipt = self._parse_receipt(content)
        if reference.artifact_id != f"{receipt.run_id}/receipt.json":
            raise VerificationEvidenceError(
                "verification receipt run ID does not match its artifact path"
            )
        return receipt

    def _parse_receipt(self, content: bytes) -> VerificationReceipt:
        try:
            raw = loads_strict_json(content, label="verification receipt")
            return VerificationReceipt.from_dict(raw)
        except ValueError as exc:
            raise VerificationEvidenceError(str(exc)) from exc

    def _read_bytes(self, artifact_id: str) -> bytes:
        parts = PurePosixPath(artifact_id).parts
        if not parts:
            raise VerificationEvidenceError("verification artifact ID is empty")
        try:
            root_fd = os.open(
                self.root,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
        except OSError as exc:
            raise VerificationEvidenceError(
                f"verification evidence root is unavailable: {self.root}"
            ) from exc
        descriptors = [root_fd]
        try:
            parent_fd = root_fd
            directory_flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            for part in parts[:-1]:
                descriptor = os.open(part, directory_flags, dir_fd=parent_fd)
                descriptors.append(descriptor)
                parent_fd = descriptor
            file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            file_fd = os.open(parts[-1], file_flags, dir_fd=parent_fd)
            descriptors.append(file_fd)
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode):
                raise VerificationEvidenceError(
                    "verification evidence must be a regular file"
                )
            if before.st_size > self.max_receipt_bytes:
                raise VerificationEvidenceError(
                    "verification receipt exceeds the configured byte limit"
                )
            chunks: list[bytes] = []
            remaining = self.max_receipt_bytes + 1
            while remaining > 0:
                chunk = os.read(file_fd, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            after = os.fstat(file_fd)
            if len(content) > self.max_receipt_bytes:
                raise VerificationEvidenceError(
                    "verification receipt exceeds the configured byte limit"
                )
            if _stat_identity(before) != _stat_identity(after) or len(content) != after.st_size:
                raise VerificationEvidenceError(
                    "verification receipt changed while it was being read"
                )
            return content
        except VerificationEvidenceError:
            raise
        except OSError as exc:
            raise VerificationEvidenceError(
                f"verification evidence cannot be read: {artifact_id}"
            ) from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )
