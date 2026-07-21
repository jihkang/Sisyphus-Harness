from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from sisyphus_harness.adapters.docker_verifier import DockerVerifierTransport
from sisyphus_harness.contracts.verification import CommandSpec
from sisyphus_harness.contracts.verification_service import (
    BundleVerificationRequest,
    VerificationProfile,
)
from sisyphus_harness.infra.workspace_bundle import FilesystemWorkspaceBundleStore

from .helpers import create_git_repo, run_git


_DOCKER_INTEGRATION = os.environ.get("SISYPHUS_DOCKER_INTEGRATION") == "1"


@unittest.skipUnless(
    _DOCKER_INTEGRATION,
    "set SISYPHUS_DOCKER_INTEGRATION=1 after building the verifier image",
)
class DockerVerifierIntegrationTests(unittest.TestCase):
    def test_real_container_enforces_runtime_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = create_git_repo(root / "repository")
            (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            run_git(repository, "add", "module.py")
            run_git(repository, "commit", "-q", "-m", "fixture")
            external_probe = root / "host-escape-probe"
            bundle_store = FilesystemWorkspaceBundleStore(root / "bundles")
            bundle = bundle_store.create(repository)
            profile = VerificationProfile(
                profile_id="container-boundary-probe",
                commands=(
                    CommandSpec(
                        name="boundary",
                        argv=(
                            "python",
                            "-c",
                            _boundary_probe(external_probe),
                        ),
                        timeout_seconds=10,
                        criteria=("container boundary holds",),
                    ),
                ),
            )
            request = BundleVerificationRequest(
                run_id="container-boundary-probe",
                workspace_bundle=bundle,
                profile=profile,
            )
            transport = DockerVerifierTransport(
                bundle_store=bundle_store.root,
                artifact_root=root / "verification",
                image=os.environ.get(
                    "SISYPHUS_VERIFIER_IMAGE",
                    "sisyphus-harness-verifier:local",
                ),
                timeout_seconds=30,
            )

            result = transport.execute(request)

            self.assertTrue(result.receipt.passed)
            self.assertTrue(result.receipt.workspace_unchanged)
            self.assertFalse(external_probe.exists())
            self.assertEqual(result.request_digest, request.request_digest)
            self.assertEqual(result.workspace_bundle_id, bundle.bundle_id)
            self.assertEqual(result.profile_digest, profile.profile_digest)
            self.assertEqual(transport.read_receipt(result.receipt_artifact), result.receipt)


def _boundary_probe(external_probe: Path) -> str:
    return f"""import os, socket
assert os.getuid() != 0
status = open('/proc/self/status', encoding='utf-8').read()
assert 'CapEff:\\t0000000000000000' in status
assert 'NoNewPrivs:\\t1' in status
root_mount = next(line for line in open('/proc/mounts', encoding='utf-8') if line.split()[1] == '/')
assert 'ro' in root_mount.split()[3].split(',')
assert len(os.listdir('/bundles')) == 2
for path in ('/rootfs-write-probe', {str(external_probe)!r}):
    try:
        open(path, 'w', encoding='utf-8').write('escape')
    except OSError:
        pass
    else:
        raise AssertionError(f'container wrote outside staging: {{path}}')
sock = socket.socket()
sock.settimeout(0.2)
try:
    sock.connect(('1.1.1.1', 53))
except OSError:
    pass
else:
    raise AssertionError('external network is reachable')
assert __import__('module').VALUE == 1
"""


if __name__ == "__main__":
    unittest.main()
