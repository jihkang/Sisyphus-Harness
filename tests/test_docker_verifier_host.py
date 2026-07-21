from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from sisyphus_harness.adapters.docker_verifier import (
    DockerVerifierError,
    DockerVerifierTransport,
    _CommandCapture,
    _DockerOutputLimitError,
)
from sisyphus_harness.adapters.receipt_observations import VerificationBindingError
from sisyphus_harness.contracts.verification import CommandSpec
from sisyphus_harness.contracts.verification_service import (
    BundleVerificationRequest,
    VerificationProfile,
    VerifierExecutionIdentity,
)
from sisyphus_harness.infra.verification_evidence import VerificationEvidenceError
from sisyphus_harness.infra.verifier_assets import (
    FilesystemVerifierAssetBundleStore,
)
from sisyphus_harness.infra.workspace_bundle import (
    FilesystemWorkspaceBundleStore,
    WorkspaceBundleError,
)

from .helpers import create_git_repo


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _completed(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class DockerVerifierHostTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        repository = create_git_repo(self.root / "repository")
        self.bundle_store = FilesystemWorkspaceBundleStore(self.root / "bundles")
        self.bundle = self.bundle_store.create(repository)
        image_id = _digest("d")
        self.identity = VerifierExecutionIdentity(
            runtime="docker",
            image_reference=image_id,
            image_id=image_id,
        )
        self.command = CommandSpec(
            name="host-check",
            argv=("python", "-c", "pass"),
            timeout_seconds=2,
            criteria=("command passes",),
        )
        self.profile = VerificationProfile(
            profile_id="host-profile",
            commands=(self.command,),
            asset_bundle=None,
            schema_version="sisyphus_harness.verification_profile.v2",
        )
        self.request = BundleVerificationRequest(
            run_id="host-run",
            workspace_bundle=self.bundle,
            profile=self.profile,
            execution_identity=self.identity,
            schema_version="sisyphus_harness.bundle_verification_request.v2",
        )
        self.transport = DockerVerifierTransport(
            bundle_store=self.bundle_store.root,
            artifact_root=self.root / "artifacts",
            image=image_id,
            timeout_seconds=2,
            max_output_bytes=1024,
        )

    def materialized_run(self, label: str) -> tuple[Path, Path, Path]:
        workspace = self.root / f"{label}-workspace"
        self.bundle_store.materialize(self.bundle, workspace)
        staging_directory = self.root / f"{label}-staging"
        staging_root = staging_directory / "artifacts"
        staging_root.mkdir(parents=True)
        return workspace, staging_root, staging_directory

    def asset_request(self, run_id: str) -> tuple[BundleVerificationRequest, Path]:
        source = self.root / f"{run_id}-asset-source"
        source.mkdir()
        (source / "check.py").write_text("print('check')\n", encoding="utf-8")
        store = FilesystemVerifierAssetBundleStore(self.root / f"{run_id}-asset-store")
        reference = store.create(source)
        view = self.root / f"{run_id}-asset-view"
        store.materialize(reference, view)
        profile = replace(self.profile, asset_bundle=reference)
        return replace(self.request, run_id=run_id, profile=profile), view

    def test_command_builder_requires_exact_command_specification(self) -> None:
        with self.assertRaisesRegex(TypeError, "exact CommandSpec"):
            self.transport.command(  # type: ignore[arg-type]
                SimpleNamespace(argv=("python",)),
                workspace=self.root / "workspace",
                cidfile=self.root / "command.cid",
                execution_identity=self.identity,
            )

    def test_executable_probe_records_success_and_candidate_launch_error(self) -> None:
        _, asset_view = self.asset_request("probe-assets")
        observed: list[tuple[str, ...]] = []

        def run(
            _: DockerVerifierTransport,
            command: list[str],
        ) -> SimpleNamespace:
            observed.append(tuple(command))
            return _completed(
                stdout=json.dumps(
                    {"path": "/usr/local/bin/python", "sha256": _digest("e")}
                )
            )

        with patch.object(
            DockerVerifierTransport,
            "_run_container",
            autospec=True,
            side_effect=run,
        ):
            resolved = self.transport._probe_executable(
                self.command,
                workspace=self.root / "repository",
                asset_view=asset_view,
                cidfile=self.root / "probe.cid",
                execution_identity=self.identity,
                timeout_seconds=1,
            )

        self.assertEqual(resolved, ("/usr/local/bin/python", _digest("e"), None))
        rendered = " ".join(observed[0])
        self.assertIn("dst=/workspace,readonly", rendered)
        self.assertIn("dst=/verifier-assets,readonly", rendered)

        with patch.object(
            DockerVerifierTransport,
            "_run_container",
            autospec=True,
            return_value=_completed(
                returncode=127,
                stdout=json.dumps({"error": "candidate executable missing"}),
            ),
        ):
            self.assertEqual(
                self.transport._probe_executable(
                    self.command,
                    workspace=self.root / "repository",
                    asset_view=None,
                    cidfile=self.root / "missing.cid",
                    execution_identity=self.identity,
                    timeout_seconds=1,
                ),
                (None, None, "candidate executable missing"),
            )

    def test_executable_probe_rejects_runtime_and_malformed_results(self) -> None:
        arguments = {
            "workspace": self.root / "repository",
            "asset_view": None,
            "cidfile": self.root / "probe-errors.cid",
            "execution_identity": self.identity,
            "timeout_seconds": 1,
        }
        failures: tuple[tuple[object, str], ...] = (
            (_DockerOutputLimitError(b"prefix", b"error"), "exceeded output"),
            (OSError("docker unavailable"), "could not start"),
        )
        for failure, message in failures:
            with self.subTest(message=message), patch.object(
                DockerVerifierTransport,
                "_run_container",
                autospec=True,
                side_effect=failure,
            ), patch.object(
                DockerVerifierTransport,
                "_remove_container",
                autospec=True,
            ) as remove:
                with self.assertRaisesRegex(DockerVerifierError, message):
                    self.transport._probe_executable(self.command, **arguments)
                remove.assert_called_once()

        results = (
            (_completed(returncode=125), "Docker could not start"),
            (_completed(stdout="not-json"), "invalid JSON"),
            (_completed(stdout='{"path": "/python"}'), "result is invalid"),
            (
                _completed(stdout=json.dumps({"path": "", "sha256": _digest("e")})),
                "path is invalid",
            ),
            (
                _completed(stdout=json.dumps({"path": "/python", "sha256": "bad"})),
                "digest is invalid",
            ),
        )
        for completed, message in results:
            with self.subTest(message=message), patch.object(
                DockerVerifierTransport,
                "_run_container",
                autospec=True,
                return_value=completed,
            ):
                with self.assertRaisesRegex(DockerVerifierError, message):
                    self.transport._probe_executable(self.command, **arguments)

    def test_command_capture_distinguishes_exit_launch_and_runtime_failure(self) -> None:
        arguments = {
            "workspace": self.root / "repository",
            "asset_view": None,
            "cidfile": self.root / "capture.cid",
            "execution_identity": self.identity,
            "timeout_seconds": 1,
        }
        with patch.object(
            DockerVerifierTransport,
            "_run_container",
            autospec=True,
            return_value=_completed(stdout="ok"),
        ):
            capture = self.transport._capture_command(self.command, **arguments)
        self.assertEqual(capture.returncode, 0)
        self.assertEqual(capture.stdout, "ok")

        for returncode in (126, 127):
            with self.subTest(returncode=returncode), patch.object(
                DockerVerifierTransport,
                "_run_container",
                autospec=True,
                return_value=_completed(returncode=returncode),
            ):
                capture = self.transport._capture_command(self.command, **arguments)
            self.assertIsNone(capture.returncode)
            self.assertIn("could not start", capture.launch_error or "")

        for failure, message in (
            (_completed(returncode=125), "Docker could not start"),
            (OSError("docker unavailable"), "container could not start"),
        ):
            with self.subTest(message=message), patch.object(
                DockerVerifierTransport,
                "_run_container",
                autospec=True,
                **(
                    {"return_value": failure}
                    if isinstance(failure, SimpleNamespace)
                    else {"side_effect": failure}
                ),
            ), patch.object(
                DockerVerifierTransport,
                "_remove_container",
                autospec=True,
            ) as remove:
                with self.assertRaisesRegex(DockerVerifierError, message):
                    self.transport._capture_command(self.command, **arguments)
                remove.assert_called_once()

    def test_host_command_records_deadline_probe_and_output_failures(self) -> None:
        captures = (
            (
                "deadline",
                time.monotonic() - 1,
                None,
                None,
                "timeout",
                "bounded deadline",
            ),
            (
                "probe-timeout",
                time.monotonic() + 10,
                subprocess.TimeoutExpired(("docker", "run"), 1),
                None,
                "timeout",
                "bounded deadline",
            ),
            (
                "probe-error",
                time.monotonic() + 10,
                (None, None, "missing executable"),
                None,
                "launch_error",
                "missing executable",
            ),
            (
                "output-limit",
                time.monotonic() + 10,
                ("/python", _digest("e"), None),
                _CommandCapture(
                    returncode=None,
                    stdout="prefix",
                    stderr="limited",
                    output_limited=True,
                ),
                "output_limit",
                "exceeded 1024 bytes",
            ),
        )
        for label, deadline, probe, capture, category, error in captures:
            workspace, staging_root, staging_directory = self.materialized_run(label)
            run_directory = staging_root / self.request.run_id
            run_directory.mkdir()
            with patch.object(
                DockerVerifierTransport,
                "_probe_executable",
                autospec=True,
                **(
                    {"side_effect": probe}
                    if isinstance(probe, BaseException)
                    else {"return_value": probe}
                ),
            ) as executable_probe, patch.object(
                DockerVerifierTransport,
                "_capture_command",
                autospec=True,
                return_value=capture,
            ) as command_capture:
                result = self.transport._execute_host_command(
                    self.request,
                    self.command,
                    index=0,
                    workspace=workspace,
                    run_directory=run_directory,
                    asset_view=None,
                    expected_asset_tree=None,
                    staging_directory=staging_directory,
                    deadline=deadline,
                )
            self.assertFalse(result.passed)
            self.assertEqual(result.failure_category, category)
            self.assertIn(error, result.error or "")
            if label == "deadline":
                executable_probe.assert_not_called()
            if label != "output-limit":
                command_capture.assert_not_called()

    def test_host_command_rejects_asset_mutation(self) -> None:
        request, asset_view = self.asset_request("asset-mutation")
        workspace, staging_root, staging_directory = self.materialized_run(
            "asset-mutation"
        )
        run_directory = staging_root / request.run_id
        run_directory.mkdir()

        def mutate_asset(*_: object, **__: object) -> _CommandCapture:
            path = asset_view / "check.py"
            path.chmod(0o644)
            path.write_text("changed\n", encoding="utf-8")
            return _CommandCapture(returncode=0, stdout="", stderr="")

        with patch.object(
            DockerVerifierTransport,
            "_probe_executable",
            autospec=True,
            return_value=("/python", _digest("e"), None),
        ), patch.object(
            DockerVerifierTransport,
            "_capture_command",
            autospec=True,
            side_effect=mutate_asset,
        ):
            with self.assertRaisesRegex(DockerVerifierError, "asset view changed"):
                self.transport._execute_host_command(
                    request,
                    self.command,
                    index=0,
                    workspace=workspace,
                    run_directory=run_directory,
                    asset_view=asset_view,
                    expected_asset_tree=request.profile.asset_bundle.tree_hash,
                    staging_directory=staging_directory,
                    deadline=time.monotonic() + 10,
                )

    def test_host_receipt_rejects_input_and_binding_substitution(self) -> None:
        workspace, staging_root, staging_directory = self.materialized_run(
            "host-input"
        )
        substituted_bundle = replace(self.bundle, tree_hash=_digest("9"))
        substituted_request = replace(
            self.request,
            workspace_bundle=substituted_bundle,
        )
        with self.assertRaisesRegex(DockerVerifierError, "workspace does not match"):
            self.transport._execute_host_owned(
                substituted_request,
                workspace=workspace,
                staging_root=staging_root,
                asset_view=None,
                staging_directory=staging_directory,
            )

        asset_request, asset_view = self.asset_request("host-asset")
        workspace, staging_root, staging_directory = self.materialized_run(
            "host-asset"
        )
        with patch(
            "sisyphus_harness.adapters.docker_verifier.verifier_asset_tree_hash",
            return_value=_digest("9"),
        ):
            with self.assertRaisesRegex(DockerVerifierError, "asset view does not match"):
                self.transport._execute_host_owned(
                    asset_request,
                    workspace=workspace,
                    staging_root=staging_root,
                    asset_view=asset_view,
                    staging_directory=staging_directory,
                )

        workspace, staging_root, staging_directory = self.materialized_run(
            "host-binding"
        )
        with patch.object(
            DockerVerifierTransport,
            "_probe_executable",
            autospec=True,
            return_value=("/python", _digest("e"), None),
        ), patch.object(
            DockerVerifierTransport,
            "_capture_command",
            autospec=True,
            return_value=_CommandCapture(returncode=0, stdout="", stderr=""),
        ), patch(
            "sisyphus_harness.adapters.docker_verifier.validate_final_verification_bindings",
            side_effect=VerificationBindingError("substituted binding"),
        ):
            with self.assertRaisesRegex(DockerVerifierError, "substituted binding"):
                self.transport._execute_host_owned(
                    self.request,
                    workspace=workspace,
                    staging_root=staging_root,
                    asset_view=None,
                    staging_directory=staging_directory,
                )

    def test_execute_closes_materialization_asset_and_published_receipt_failures(
        self,
    ) -> None:
        with patch.object(
            FilesystemWorkspaceBundleStore,
            "materialize",
            autospec=True,
            side_effect=WorkspaceBundleError("invalid archive"),
        ):
            with self.assertRaisesRegex(DockerVerifierError, "isolated materialization"):
                self.transport.execute(self.request)

        with patch.object(
            FilesystemWorkspaceBundleStore,
            "materialize",
            autospec=True,
            return_value=_digest("9"),
        ):
            with self.assertRaisesRegex(DockerVerifierError, "does not match"):
                self.transport.execute(self.request)

        asset_request, _ = self.asset_request("missing-store")
        with self.assertRaisesRegex(DockerVerifierError, "requires an asset bundle store"):
            self.transport.execute(asset_request)

        with patch.object(
            DockerVerifierTransport,
            "_probe_executable",
            autospec=True,
            return_value=("/python", _digest("e"), None),
        ), patch.object(
            DockerVerifierTransport,
            "_capture_command",
            autospec=True,
            return_value=_CommandCapture(returncode=0, stdout="", stderr=""),
        ), patch.object(
            DockerVerifierTransport,
            "read_receipt",
            autospec=True,
            side_effect=VerificationEvidenceError("published receipt changed"),
        ):
            with self.assertRaisesRegex(DockerVerifierError, "published.*validation"):
                self.transport.execute(replace(self.request, run_id="published-invalid"))

    def test_legacy_result_parser_fails_closed_on_non_protocol_output(self) -> None:
        cases = (
            (_completed(returncode=125, stderr="docker failed"), "docker failed"),
            (_completed(returncode=125), "verifier container failed"),
            (_completed(stdout="\n"), "returned no result"),
            (_completed(stdout="not-json"), "invalid JSON"),
        )
        for completed, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(DockerVerifierError, message):
                    self.transport._parse_result(completed, self.request)


if __name__ == "__main__":
    unittest.main()
