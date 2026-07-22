from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Callable

from ..contracts.verification_service import BundleVerificationRequest
from ..receipts import write_json_atomic
from .docker_runtime import DockerVerifierError


@dataclass(frozen=True, slots=True)
class DockerEvidencePublisher:
    artifact_root: Path

    def publish(
        self,
        staging_root: Path,
        request: BundleVerificationRequest,
        *,
        write_json: Callable[[Path, object], None] = write_json_atomic,
        replace_path: Callable[[Path, Path], None] = os.replace,
        fsync_directory: Callable[[Path], None],
    ) -> None:
        source = staging_root / request.run_id
        destination = self.artifact_root / request.run_id
        if source.is_symlink() or not source.is_dir():
            raise DockerVerifierError(
                "verifier did not create a regular run directory"
            )
        lock_path = self.artifact_root / f".{request.run_id}.publish.lock"
        request_path = (
            self.artifact_root / "service-requests" / f"{request.run_id}.json"
        )
        request_written = False
        try:
            lock_descriptor = os.open(
                lock_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError as exc:
            raise DockerVerifierError(
                "verification run is already being published"
            ) from exc
        try:
            if destination.exists() or destination.is_symlink():
                raise DockerVerifierError("verification run already exists")
            if request_path.exists() or request_path.is_symlink():
                raise DockerVerifierError(
                    "verification service request record already exists"
                )
            write_json(request_path, request.to_dict())
            request_written = True
            replace_path(source, destination)
            fsync_directory(self.artifact_root)
        except DockerVerifierError:
            raise
        except OSError as exc:
            if request_written and not destination.exists():
                _remove_uncommitted_request(
                    request_path,
                    fsync_directory=fsync_directory,
                )
            raise DockerVerifierError(
                "verification run could not be published"
            ) from exc
        finally:
            os.close(lock_descriptor)
            lock_path.unlink(missing_ok=True)
            fsync_directory(self.artifact_root)


def _remove_uncommitted_request(
    path: Path,
    *,
    fsync_directory: Callable[[Path], None],
) -> None:
    try:
        path.unlink(missing_ok=True)
        fsync_directory(path.parent)
    except OSError:
        return


def fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
