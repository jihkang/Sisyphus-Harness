from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sisyphus_harness.adapters.docker_verifier import DockerVerifierTransport
from sisyphus_harness.infra.workspace_bundle import FilesystemWorkspaceBundleStore
from sisyphus_harness.ports.verification_service import (
    VerificationExecutorPort,
    VerificationServicePort,
)
from sisyphus_harness.services.verifier import BundleVerifierService


class VerificationServicePortTests(unittest.TestCase):
    def test_executor_and_host_transport_have_distinct_identity_responsibility(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = BundleVerifierService(
                bundle_store=FilesystemWorkspaceBundleStore(root / "bundles"),
                artifact_root=root / "artifacts",
                work_root=root / "work",
            )
            container = DockerVerifierTransport(
                bundle_store=root / "bundles",
                artifact_root=root / "artifacts",
            )

            self.assertIsInstance(local, VerificationExecutorPort)
            self.assertNotIsInstance(local, VerificationServicePort)
            self.assertIsInstance(container, VerificationServicePort)


if __name__ == "__main__":
    unittest.main()
