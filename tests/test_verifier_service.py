from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

from sisyphus_harness.contracts import (
    BundleVerificationRequest,
    CommandSpec,
    VerificationProfile,
    VerificationServiceResult,
)
from sisyphus_harness.infra.workspace_bundle import FilesystemWorkspaceBundleStore
from sisyphus_harness.services.verifier import BundleVerifierService

from .helpers import create_git_repo


class BundleVerifierServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.repository = create_git_repo(self.root / "repository")
        self.bundle_store = FilesystemWorkspaceBundleStore(self.root / "bundles")
        self.bundle = self.bundle_store.create(self.repository)
        self.artifacts = self.root / "artifacts"
        self.work = self.root / "work"

    def request(self, code: str, *, run_id: str) -> BundleVerificationRequest:
        return BundleVerificationRequest(
            run_id=run_id,
            workspace_bundle=self.bundle,
            profile=VerificationProfile(
                profile_id="unit-tests",
                commands=(
                    CommandSpec(
                        name="check",
                        argv=(sys.executable, "-c", code),
                        timeout_seconds=5,
                        criteria=("check passes",),
                    ),
                ),
            ),
        )

    def service(self) -> BundleVerifierService:
        return BundleVerifierService(
            bundle_store=self.bundle_store,
            artifact_root=self.artifacts,
            work_root=self.work,
        )

    def test_service_materializes_bundle_and_returns_strict_evidence(self) -> None:
        request = self.request(
            "from pathlib import Path; assert Path('tracked.txt').read_text() == 'baseline\\n'",
            run_id="service-pass",
        )

        result = self.service().execute(request)
        parsed = VerificationServiceResult.from_dict(result.to_dict())

        self.assertTrue(parsed.receipt.passed)
        self.assertEqual(parsed.request_digest, request.request_digest)
        self.assertEqual(parsed.workspace_bundle_id, self.bundle.bundle_id)
        self.assertEqual(parsed.profile_digest, request.profile.profile_digest)
        self.assertTrue(
            (self.artifacts / "service-pass" / "receipt.json").is_file()
        )
        self.assertTrue(
            (
                self.artifacts
                / "service-requests"
                / "service-pass.json"
            ).is_file()
        )
        self.assertEqual(list(self.work.iterdir()), [])

    def test_service_detects_mutation_in_bundle_workspace(self) -> None:
        result = self.service().execute(
            self.request(
                "from pathlib import Path; Path('tracked.txt').write_text('changed\\n')",
                run_id="service-mutation",
            )
        )

        self.assertFalse(result.receipt.passed)
        self.assertFalse(result.receipt.workspace_unchanged)
        self.assertEqual(
            result.receipt.commands[0].failure_category,
            "workspace_mutation",
        )

    def test_service_request_parser_rejects_digest_tampering(self) -> None:
        payload = self.request("pass", run_id="tampered").to_dict()
        payload["run_id"] = "different"

        with self.assertRaisesRegex(ValueError, "digest does not match"):
            BundleVerificationRequest.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
