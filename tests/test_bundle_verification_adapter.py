from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import unittest

from sisyphus_harness.adapters.bundle_verification import BundleVerificationAdapter
from sisyphus_harness.contracts.verification import CommandSpec
from sisyphus_harness.contracts.verification_service import VerifierExecutionIdentity
from sisyphus_harness.infra.workspace_bundle import FilesystemWorkspaceBundleStore
from sisyphus_harness.services.verifier import BundleVerifierService

from .helpers import create_git_repo, run_git


class _TimeoutRecordingService:
    def __init__(self, delegate: BundleVerifierService) -> None:
        self.delegate = delegate
        self.timeout_seconds: float | None = None

    def execution_identity(self) -> VerifierExecutionIdentity:
        return VerifierExecutionIdentity(
            runtime="docker",
            image_reference="verifier:test",
            image_id="sha256:" + "a" * 64,
        )

    def execute(self, request):
        raise AssertionError("deadline-aware execution must use the bounded transport")

    def execute_with_timeout(self, request, *, timeout_seconds: float):
        self.timeout_seconds = timeout_seconds
        return self.delegate.execute(request)

    def read_receipt(self, reference):
        return self.delegate.read_receipt(reference)


class BundleVerificationAdapterTests(unittest.TestCase):
    def test_verification_runs_from_an_immutable_materialized_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = create_git_repo(root / "repository")
            (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            run_git(repository, "add", "module.py")
            run_git(repository, "commit", "-q", "-m", "fixture")
            bundle_store = FilesystemWorkspaceBundleStore(root / "bundles")
            service = _TimeoutRecordingService(
                BundleVerifierService(
                    bundle_store=bundle_store,
                    artifact_root=root / "verification",
                    work_root=root / "work",
                )
            )
            adapter = BundleVerificationAdapter(bundle_store, service)
            command = CommandSpec(
                name="behavior",
                argv=(sys.executable, "-c", "from module import VALUE; assert VALUE == 1"),
                timeout_seconds=5,
                criteria=("value is one",),
            )

            receipt = adapter.verify(
                repository,
                (command,),
                run_id="bundle-adapter-test",
                deadline_monotonic=time.monotonic() + 10,
            )
            reference = adapter.receipt_reference(receipt.run_id)

            self.assertTrue(receipt.passed)
            self.assertTrue(receipt.workspace_unchanged)
            self.assertIsNotNone(service.timeout_seconds)
            self.assertGreater(service.timeout_seconds, 0)
            self.assertLessEqual(service.timeout_seconds, 10)
            self.assertEqual(adapter.read_receipt(reference), receipt)
            self.assertEqual((repository / "module.py").read_text(), "VALUE = 1\n")


if __name__ == "__main__":
    unittest.main()
