from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

from sisyphus_harness.contracts import CommandSpec
from sisyphus_harness.infra.verification_evidence import (
    FilesystemVerificationEvidenceStore,
    VerificationEvidenceError,
)
from sisyphus_harness.ports import VerificationEvidencePort
from sisyphus_harness.verifier import BoundedVerifier

from .helpers import create_git_repo


class VerificationEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        root = Path(self.temporary_directory.name)
        self.repository = create_git_repo(root / "repository")
        self.artifacts = root / "artifacts"
        self.verifier = BoundedVerifier(self.artifacts)
        self.command = CommandSpec(
            name="tests",
            argv=(sys.executable, "-c", "pass"),
            timeout_seconds=5,
            criteria=("tests pass",),
        )

    def test_verifier_exposes_digest_bound_receipt_evidence(self) -> None:
        receipt = self.verifier.verify(
            self.repository,
            (self.command,),
            run_id="verified",
        )

        reference = self.verifier.receipt_reference("verified")
        loaded = self.verifier.read_receipt(reference)

        self.assertIsInstance(self.verifier, VerificationEvidencePort)
        self.assertEqual(loaded, receipt)
        self.assertEqual(loaded.receipt_digest, receipt.receipt_digest)
        self.assertEqual(
            loaded.request_digest,
            receipt.request_digest,
        )
        self.assertTrue((self.artifacts / "verified" / "request.json").is_file())

    def test_byte_tampering_is_rejected_before_receipt_is_trusted(self) -> None:
        self.verifier.verify(
            self.repository,
            (self.command,),
            run_id="tampered",
        )
        reference = self.verifier.receipt_reference("tampered")
        receipt_path = self.artifacts / "tampered" / "receipt.json"
        receipt_path.write_bytes(
            receipt_path.read_bytes().replace(
                b'"passed": true',
                b'"passed":false',
                1,
            )
        )

        with self.assertRaisesRegex(VerificationEvidenceError, "digest does not match"):
            self.verifier.read_receipt(reference)

    def test_symlinked_receipt_and_oversized_receipt_fail_closed(self) -> None:
        self.verifier.verify(
            self.repository,
            (self.command,),
            run_id="unsafe",
        )
        receipt_path = self.artifacts / "unsafe" / "receipt.json"
        outside = Path(self.temporary_directory.name) / "outside.json"
        outside.write_text(receipt_path.read_text(encoding="utf-8"), encoding="utf-8")
        receipt_path.unlink()
        receipt_path.symlink_to(outside)

        with self.assertRaisesRegex(VerificationEvidenceError, "cannot be read"):
            self.verifier.receipt_reference("unsafe")

        oversized_root = Path(self.temporary_directory.name) / "oversized"
        (oversized_root / "run").mkdir(parents=True)
        (oversized_root / "run" / "receipt.json").write_bytes(b"{}")
        store = FilesystemVerificationEvidenceStore(
            oversized_root,
            max_receipt_bytes=1,
        )
        with self.assertRaisesRegex(VerificationEvidenceError, "byte limit"):
            store.receipt_reference("run")


if __name__ == "__main__":
    unittest.main()
