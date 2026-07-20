from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

from ..contracts.artifacts import ArtifactRef
from ..contracts.codec import loads_strict_json
from ..contracts.verification import VerificationReceipt
from ..contracts.verification_service import (
    BundleVerificationRequest,
    VerificationServiceResult,
)
from ..infra.workspace_bundle import FilesystemWorkspaceBundleStore
from ..receipts import write_json_atomic
from ..verifier import BoundedVerifier
from ..workspace_state_adapters import TreeHashWorkspaceStateAdapter


class VerifierServiceError(RuntimeError):
    pass


class BundleVerifierService:
    def __init__(
        self,
        *,
        bundle_store: FilesystemWorkspaceBundleStore,
        artifact_root: Path,
        work_root: Path,
    ) -> None:
        self.bundle_store = bundle_store
        self.artifact_root = artifact_root
        self.work_root = work_root

    def read_receipt(self, reference: ArtifactRef) -> VerificationReceipt:
        return BoundedVerifier(self.artifact_root).read_receipt(reference)

    def execute(self, request: BundleVerificationRequest) -> VerificationServiceResult:
        stored = self.bundle_store.load(request.workspace_bundle.bundle_id)
        if stored != request.workspace_bundle:
            raise VerifierServiceError(
                "workspace bundle reference does not match stored authority"
            )
        self.work_root.mkdir(parents=True, exist_ok=True)
        attempt_root = Path(
            tempfile.mkdtemp(
                prefix=f"verify-{request.run_id}-",
                dir=self.work_root,
            )
        )
        workspace = attempt_root / "workspace"
        try:
            materialized_hash = self.bundle_store.materialize(stored, workspace)
            if materialized_hash != stored.tree_hash:
                raise VerifierServiceError(
                    "materialized workspace does not match its bundle tree"
                )
            write_json_atomic(
                self.artifact_root
                / "service-requests"
                / f"{request.run_id}.json",
                request.to_dict(),
            )
            verifier = BoundedVerifier(
                self.artifact_root,
                workspace_state=TreeHashWorkspaceStateAdapter(
                    stored.source_commit_sha
                ),
            )
            receipt = verifier.verify(
                workspace,
                request.profile.commands,
                run_id=request.run_id,
                request_digest=request.request_digest,
            )
            reference = verifier.receipt_reference(receipt.run_id)
            return VerificationServiceResult(
                request_digest=request.request_digest,
                workspace_bundle_id=stored.bundle_id,
                profile_digest=request.profile.profile_digest,
                receipt=receipt,
                receipt_artifact=reference,
            )
        finally:
            shutil.rmtree(attempt_root, ignore_errors=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sisyphus-verifier-service")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--bundle-store", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--result", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        content = args.request.read_bytes()
        if len(content) > 4 * 1024 * 1024:
            raise VerifierServiceError("verification service request exceeds byte limit")
        request = BundleVerificationRequest.from_dict(
            loads_strict_json(content, label="verification service request")
        )
        result = BundleVerifierService(
            bundle_store=FilesystemWorkspaceBundleStore(args.bundle_store),
            artifact_root=args.artifact_root,
            work_root=args.work_root,
        ).execute(request)
        payload = result.to_dict()
        if args.result is not None:
            write_json_atomic(args.result, payload)
        print(json.dumps(payload, sort_keys=True))
        return 0 if result.receipt.passed else 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "schema_version": "sisyphus_harness.service_error.v1",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
