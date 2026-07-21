from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from sisyphus_harness.adapters import DockerVerifierError, DockerVerifierTransport
from sisyphus_harness.contracts import (
    ArtifactRef,
    BundleVerificationRequest,
    CommandSpec,
    VerificationProfile,
    VerificationReceipt,
    VerificationServiceResult,
    WorkspaceBundleRef,
)


class DockerVerifierTransportTests(unittest.TestCase):
    def test_transport_rejects_unbounded_runtime_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = (
                {"timeout_seconds": 0},
                {"timeout_seconds": math.inf},
                {"max_output_bytes": 0},
                {"max_output_bytes": True},
                {"image": "--privileged"},
                {"image": "unsafe image"},
            )
            for settings in cases:
                with self.subTest(settings=settings):
                    with self.assertRaises(ValueError):
                        DockerVerifierTransport(
                            bundle_store=root / "bundles",
                            artifact_root=root / "artifacts",
                            **settings,
                        )

    def test_deadline_override_clamps_transport_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport = DockerVerifierTransport(
                bundle_store=root / "bundles",
                artifact_root=root / "artifacts",
                timeout_seconds=30,
            )
            request = SimpleNamespace()
            expected = SimpleNamespace()
            with patch.object(
                DockerVerifierTransport,
                "execute",
                autospec=True,
                return_value=expected,
            ) as execute:
                result = transport.execute_with_timeout(
                    request,
                    timeout_seconds=1.25,
                )

        self.assertIs(result, expected)
        bounded, observed_request = execute.call_args.args
        self.assertIs(observed_request, request)
        self.assertEqual(bounded.timeout_seconds, 1.25)

        for invalid in (0, math.inf):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    transport.execute_with_timeout(request, timeout_seconds=invalid)

    def test_command_enforces_declared_sandbox_and_mount_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundles = root / "bundles"
            artifacts = root / "artifacts"
            request = root / "request.json"
            staging = root / "staging"
            cidfile = root / "container.cid"
            for path in (bundles, artifacts, staging):
                path.mkdir()
            request.write_text("{}", encoding="utf-8")
            command = DockerVerifierTransport(
                bundle_store=bundles,
                artifact_root=artifacts,
            ).command(
                request,
                staging_root=staging,
                bundle_view=bundles,
                cidfile=cidfile,
            )

        rendered = " ".join(command)
        self.assertIn("--network none", rendered)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop ALL", rendered)
        self.assertIn("no-new-privileges:true", rendered)
        self.assertIn("--pids-limit 64", rendered)
        self.assertIn("--memory 512m", rendered)
        self.assertIn("--cpus 1.0", rendered)
        self.assertIn(f"--cidfile {cidfile.resolve()}", rendered)
        self.assertIn("dst=/bundles,readonly", rendered)
        self.assertIn("dst=/request.json,readonly", rendered)
        self.assertIn(f"src={staging.resolve()},dst=/artifacts", rendered)
        self.assertNotIn(f"src={artifacts.resolve()},dst=/artifacts", rendered)
        self.assertNotIn("dst=/artifacts,readonly", rendered)
        self.assertIn("/tmp:rw,noexec,nosuid,nodev", rendered)
        self.assertIn("/work:rw,exec,nosuid,nodev", rendered)
        self.assertIn("--work-root /work", rendered)
        if hasattr(os, "getuid"):
            self.assertIn(f"--user {os.getuid()}:{os.getgid()}", rendered)

    def test_command_quotes_mount_sources_containing_commas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source,with,commas"
            bundles = root / "bundles"
            staging = root / "staging"
            request = root / "request.json"
            command = DockerVerifierTransport(
                bundle_store=bundles,
                artifact_root=root / "artifacts",
            ).command(
                request,
                staging_root=staging,
                bundle_view=bundles,
                cidfile=root / "container.cid",
            )

        mount_values = [
            command[index + 1]
            for index, value in enumerate(command[:-1])
            if value == "--mount"
        ]
        parsed = [next(csv.reader([value])) for value in mount_values]
        self.assertIn(f"src={bundles.resolve()}", parsed[0])
        self.assertIn(f"src={staging.resolve()}", parsed[1])
        self.assertIn(f"src={request.resolve()}", parsed[2])

    def test_execute_rejects_result_bound_to_another_workspace(self) -> None:
        digest = "sha256:" + "1" * 64
        bundle = WorkspaceBundleRef(
            bundle_id=f"workspace:{digest}",
            archive_sha256=digest,
            size_bytes=1,
            source_commit_sha="2" * 40,
            source_state_hash="sha256:" + "3" * 64,
            tree_hash="sha256:" + "4" * 64,
            changed_paths=(),
            entry_count=0,
        )
        profile = VerificationProfile(
            profile_id="unit",
            commands=(
                CommandSpec(
                    name="check",
                    argv=("python", "-c", "pass"),
                    timeout_seconds=1,
                    criteria=("legacy projection",),
                ),
            ),
        )
        request = BundleVerificationRequest(
            run_id="run-1",
            workspace_bundle=bundle,
            profile=profile,
        )
        receipt = VerificationReceipt(
            run_id="run-1",
            workspace="/workspace",
            worktree_commit_sha="2" * 40,
            started_at="2026-07-20T00:00:00Z",
            finished_at="2026-07-20T00:00:01Z",
            passed=True,
            commands=(),
            workspace_state_before=bundle.tree_hash,
            workspace_state_after=bundle.tree_hash,
            workspace_unchanged=True,
            request_digest=request.request_digest,
        )
        result = VerificationServiceResult(
            request_digest=request.request_digest,
            workspace_bundle_id="workspace:sha256:" + "9" * 64,
            profile_digest=profile.profile_digest,
            receipt=receipt,
            receipt_artifact=ArtifactRef(
                artifact_id="run-1/receipt.json",
                sha256="sha256:" + "5" * 64,
                size_bytes=1,
                media_type=(
                    "application/vnd.sisyphus-harness.verification-receipt+json"
                ),
            ),
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport = DockerVerifierTransport(
                bundle_store=root / "bundles",
                artifact_root=root / "artifacts",
            )
            completed = SimpleNamespace(
                returncode=0,
                stdout=json.dumps(result.to_dict()),
                stderr="",
            )
            with self.assertRaisesRegex(
                DockerVerifierError,
                "different workspace bundle",
            ):
                transport._parse_result(completed, request)


if __name__ == "__main__":
    unittest.main()
