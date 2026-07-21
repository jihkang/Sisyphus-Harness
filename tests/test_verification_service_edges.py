from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from sisyphus_harness.adapters.docker_verifier import (
    DockerVerifierError,
    DockerVerifierTransport,
    _CommandCapture,
)
from sisyphus_harness.contracts.artifacts import ArtifactRef
from sisyphus_harness.contracts.verification import (
    CommandResult,
    CommandSpec,
    VerificationReceipt,
)
from sisyphus_harness.contracts.verification_service import (
    BundleVerificationRequest,
    VerificationProfile,
    VerificationServiceResult,
    VerifierExecutionIdentity,
)
from sisyphus_harness.contracts.verifier_assets import VerifierAssetBundleRef
from sisyphus_harness.contracts.workspace import WorkspaceBundleRef
from sisyphus_harness.infra.verifier_assets import (
    FilesystemVerifierAssetBundleStore,
)
from sisyphus_harness.infra.verification_evidence import (
    VERIFICATION_RECEIPT_MEDIA_TYPE,
    FilesystemVerificationEvidenceStore,
    VerificationEvidenceError,
)
from sisyphus_harness.infra.workspace_bundle import FilesystemWorkspaceBundleStore
from sisyphus_harness.services.verifier import (
    BundleVerifierService,
    VerifierServiceError,
    _parser,
    main,
)

from .helpers import create_git_repo


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _bundle(character: str = "1") -> WorkspaceBundleRef:
    archive_digest = _digest(character)
    return WorkspaceBundleRef(
        bundle_id=f"workspace:{archive_digest}",
        archive_sha256=archive_digest,
        size_bytes=1,
        source_commit_sha="2" * 40,
        source_state_hash=_digest("3"),
        tree_hash=_digest("4"),
        changed_paths=(),
        entry_count=0,
    )


def _identity() -> VerifierExecutionIdentity:
    image_id = _digest("d")
    return VerifierExecutionIdentity(
        runtime="docker",
        image_reference=image_id,
        image_id=image_id,
    )


def _profile(profile_id: str = "unit") -> VerificationProfile:
    return VerificationProfile(
        profile_id=profile_id,
        commands=(
            CommandSpec(
                name="check",
                argv=(sys.executable, "-c", "pass"),
                timeout_seconds=2,
                criteria=("command completes",),
            ),
        ),
        asset_bundle=None,
        schema_version="sisyphus_harness.verification_profile.v2",
    )


def _request(
    *,
    run_id: str = "edge-run",
    bundle: WorkspaceBundleRef | None = None,
    profile: VerificationProfile | None = None,
) -> BundleVerificationRequest:
    return BundleVerificationRequest(
        run_id=run_id,
        workspace_bundle=bundle or _bundle(),
        profile=profile or _profile(),
        execution_identity=_identity(),
        schema_version="sisyphus_harness.bundle_verification_request.v2",
    )


def _receipt(
    request: BundleVerificationRequest,
    *,
    passed: bool = True,
    run_id: str | None = None,
) -> VerificationReceipt:
    command = request.profile.commands[0]
    unchanged = True
    command_result = CommandResult(
        name=command.name,
        argv=command.argv,
        criteria=command.criteria,
        passed=passed,
        timed_out=False,
        exit_code=0 if passed else 1,
        duration_ms=1,
        executable_path=sys.executable,
        executable_sha256=_digest("e"),
        stdout_path=f"00-{command.name}/stdout.txt",
        stderr_path=f"00-{command.name}/stderr.txt",
        workspace_state_before=request.workspace_bundle.tree_hash,
        workspace_state_after=request.workspace_bundle.tree_hash,
        workspace_unchanged=True,
        failure_category=None if passed else "assertion_failure",
        error=None,
    )
    return VerificationReceipt(
        run_id=run_id or request.run_id,
        workspace="/workspace",
        worktree_commit_sha=request.workspace_bundle.source_commit_sha,
        started_at="2026-07-20T00:00:00Z",
        finished_at="2026-07-20T00:00:01Z",
        passed=passed,
        commands=(command_result,),
        workspace_state_before=request.workspace_bundle.tree_hash,
        workspace_state_after=(
            request.workspace_bundle.tree_hash if unchanged else _digest("9")
        ),
        workspace_unchanged=unchanged,
        request_digest=request.request_digest,
        schema_version="sisyphus_harness.verification.v3",
        workspace_bundle_id=request.workspace_bundle.bundle_id,
        profile_digest=request.profile.profile_digest,
        execution_identity_digest=request.execution_identity.identity_digest,
        verifier_asset_bundle_id=(
            request.profile.asset_bundle.bundle_id
            if request.profile.asset_bundle is not None
            else None
        ),
    )


def _result(
    request: BundleVerificationRequest,
    *,
    passed: bool = True,
    workspace_bundle_id: str | None = None,
    profile_digest: str | None = None,
) -> VerificationServiceResult:
    receipt = _receipt(request, passed=passed)
    receipt_content = (
        json.dumps(receipt.to_dict(), indent=2, sort_keys=True) + "\n"
    ).encode()
    result = VerificationServiceResult(
        request_digest=request.request_digest,
        workspace_bundle_id=request.workspace_bundle.bundle_id,
        profile_digest=request.profile.profile_digest,
        receipt=receipt,
        receipt_artifact=ArtifactRef(
            artifact_id=f"{request.run_id}/receipt.json",
            sha256=f"sha256:{hashlib.sha256(receipt_content).hexdigest()}",
            size_bytes=len(receipt_content),
            media_type=VERIFICATION_RECEIPT_MEDIA_TYPE,
        ),
        execution_identity=request.execution_identity,
        schema_version="sisyphus_harness.verification_service_result.v2",
    )
    if workspace_bundle_id is not None:
        object.__setattr__(result, "workspace_bundle_id", workspace_bundle_id)
    if profile_digest is not None:
        object.__setattr__(result, "profile_digest", profile_digest)
    return result


def _completed(
    *,
    stdout: str,
    returncode: int = 0,
    stderr: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )


class DockerVerifierTransportEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        root = Path(self.temporary_directory.name)
        self.root = root
        repository = create_git_repo(root / "repository")
        bundle_store = root / "bundles"
        bundle = FilesystemWorkspaceBundleStore(bundle_store).create(repository)
        self.transport = DockerVerifierTransport(
            bundle_store=bundle_store,
            artifact_root=root / "artifacts",
            image=_digest("d"),
            timeout_seconds=0.1,
        )
        self.request = _request(bundle=bundle)
        self.observed_bundle_names: tuple[str, ...] = ()
        self.observed_bundle_source: Path | None = None
        self.observed_asset_names: tuple[str, ...] = ()
        self.observed_command: tuple[str, ...] = ()

    def execute_with(
        self,
        completed: SimpleNamespace,
        *,
        transport: DockerVerifierTransport | None = None,
        request: BundleVerificationRequest | None = None,
    ) -> VerificationServiceResult:
        active_transport = transport or self.transport
        active_request = request or self.request

        def capture(
            _: DockerVerifierTransport,
            specification: CommandSpec,
            *,
            workspace: Path,
            asset_view: Path | None,
            cidfile: Path,
            execution_identity: VerifierExecutionIdentity,
            timeout_seconds: float,
        ) -> _CommandCapture:
            del timeout_seconds
            self.observed_bundle_source = workspace
            self.observed_bundle_names = tuple(
                sorted(
                    path.relative_to(workspace).as_posix()
                    for path in workspace.rglob("*")
                    if path.is_file()
                )
            )
            if asset_view is not None:
                self.observed_asset_names = tuple(
                    sorted(
                        path.relative_to(asset_view).as_posix()
                        for path in asset_view.rglob("*")
                        if path.is_file()
                    )
                )
            self.observed_command = tuple(
                active_transport.command(
                    specification,
                    workspace=workspace,
                    asset_view=asset_view,
                    cidfile=cidfile,
                    execution_identity=execution_identity,
                )
            )
            if completed.returncode == 125:
                raise DockerVerifierError(
                    completed.stderr.strip()[-2000:]
                    or "Docker could not start the verifier command container"
                )
            launch_error = None
            returncode = completed.returncode
            if returncode in {126, 127}:
                launch_error = completed.stderr.strip() or "launch failed"
                returncode = None
            return _CommandCapture(
                returncode=returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                launch_error=launch_error,
                duration_ms=1,
            )

        with patch.object(
            DockerVerifierTransport,
            "_probe_executable",
            autospec=True,
            return_value=(sys.executable, _digest("e"), None),
        ), patch.object(
            DockerVerifierTransport,
            "_capture_command",
            autospec=True,
            side_effect=capture,
        ):
            return active_transport.execute(active_request)

    def asset_request(
        self,
    ) -> tuple[
        DockerVerifierTransport,
        BundleVerificationRequest,
        VerifierAssetBundleRef,
    ]:
        source = self.root / "asset-source"
        source.mkdir()
        (source / "check.py").write_text("print('checked')\n", encoding="utf-8")
        (source / "fixture.txt").write_text("operator fixture\n", encoding="utf-8")
        store = FilesystemVerifierAssetBundleStore(self.root / "asset-store")
        reference = store.create(source)
        profile = VerificationProfile(
            profile_id="asset-profile",
            commands=self.request.profile.commands,
            asset_bundle=reference,
            schema_version="sisyphus_harness.verification_profile.v2",
        )
        request = _request(
            run_id="asset-run",
            bundle=self.request.workspace_bundle,
            profile=profile,
        )
        return replace(self.transport, asset_store=store.root), request, reference

    def test_image_tag_resolves_once_to_an_immutable_identity(self) -> None:
        transport = replace(self.transport, image="verifier:test")
        with patch(
            "sisyphus_harness.adapters.docker_verifier.subprocess.run",
            return_value=_completed(stdout=_digest("f") + "\n"),
        ) as inspect:
            identity = transport.execution_identity()

        self.assertEqual(identity.image_reference, "verifier:test")
        self.assertEqual(identity.image_id, _digest("f"))
        self.assertEqual(
            inspect.call_args.args[0],
            (
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                "verifier:test",
            ),
        )

        request = replace(self.request, execution_identity=identity)
        with patch.object(
            DockerVerifierTransport,
            "execution_identity",
            autospec=True,
            return_value=identity,
        ):
            self.execute_with(
                _completed(stdout=json.dumps(_result(request).to_dict())),
                transport=transport,
                request=request,
            )
        self.assertIn(identity.image_id, self.observed_command)
        self.assertNotIn(identity.image_reference, self.observed_command)

    def test_image_identity_resolution_failures_are_closed(self) -> None:
        transport = replace(self.transport, image="verifier:test")
        failures = (
            OSError("docker unavailable"),
            subprocess.TimeoutExpired(("docker", "image", "inspect"), 30),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), patch(
                "sisyphus_harness.adapters.docker_verifier.subprocess.run",
                side_effect=failure,
            ):
                with self.assertRaisesRegex(
                    DockerVerifierError,
                    "identity could not be resolved",
                ):
                    transport.execution_identity()

        invalid_results = (
            _completed(stdout=_digest("f"), returncode=1),
            _completed(stdout="not-a-digest\n"),
            _completed(stdout=_digest("f") + "\n" + _digest("e") + "\n"),
        )
        for completed in invalid_results:
            with self.subTest(result=completed), patch(
                "sisyphus_harness.adapters.docker_verifier.subprocess.run",
                return_value=completed,
            ):
                with self.assertRaisesRegex(
                    DockerVerifierError,
                    "identity could not be resolved",
                ):
                    transport.execution_identity()

    def test_legacy_request_is_rejected_before_identity_or_container_use(self) -> None:
        legacy_profile = VerificationProfile(
            profile_id="legacy",
            commands=self.request.profile.commands,
        )
        legacy = BundleVerificationRequest(
            run_id="legacy-run",
            workspace_bundle=self.request.workspace_bundle,
            profile=legacy_profile,
        )
        with patch.object(
            DockerVerifierTransport,
            "execution_identity",
            autospec=True,
        ) as identity, patch.object(
            DockerVerifierTransport,
            "_run_container",
            autospec=True,
        ) as run:
            with self.assertRaisesRegex(DockerVerifierError, "v2 request"):
                self.transport.execute(legacy)
            identity.assert_not_called()
            run.assert_not_called()

    def test_non_directory_bundle_stores_fail_before_container_start(self) -> None:
        outside = self.root / "outside-bundles"
        outside.mkdir()
        candidates = (
            self.root / "bundle-file",
            self.root / "bundle-link",
            self.root / "missing-bundles",
        )
        candidates[0].write_text("not a directory", encoding="utf-8")
        candidates[1].symlink_to(outside, target_is_directory=True)

        for bundle_store in candidates:
            with self.subTest(bundle_store=bundle_store), patch.object(
                DockerVerifierTransport,
                "_run_container",
                autospec=True,
            ) as run:
                with self.assertRaisesRegex(
                    DockerVerifierError,
                    "not a regular directory",
                ):
                    replace(self.transport, bundle_store=bundle_store).execute(
                        self.request
                    )
                run.assert_not_called()

    def test_bundle_store_open_identity_and_reference_json_fail_closed(self) -> None:
        with patch(
            "sisyphus_harness.adapters.docker_verifier._same_stable_file",
            return_value=False,
        ), patch.object(
            DockerVerifierTransport,
            "_run_container",
            autospec=True,
        ) as run:
            with self.assertRaisesRegex(DockerVerifierError, "changed while being opened"):
                self.transport.execute(self.request)
            run.assert_not_called()

        digest = self.request.workspace_bundle.archive_sha256.removeprefix("sha256:")
        reference_path = self.transport.bundle_store / f"{digest}.json"
        reference_path.write_bytes(b"not-json")
        with patch.object(
            DockerVerifierTransport,
            "_run_container",
            autospec=True,
        ) as run:
            with self.assertRaisesRegex(
                DockerVerifierError,
                "reference failed host validation",
            ):
                self.transport.execute(self.request)
            run.assert_not_called()

    def test_image_tag_drift_fails_before_container_start(self) -> None:
        admitted = VerifierExecutionIdentity(
            runtime="docker",
            image_reference="verifier:test",
            image_id=_digest("d"),
        )
        changed = replace(admitted, image_id=_digest("f"))
        transport = replace(self.transport, image="verifier:test")
        request = replace(self.request, execution_identity=admitted)

        with patch.object(
            DockerVerifierTransport,
            "execution_identity",
            autospec=True,
            return_value=changed,
        ), patch.object(
            DockerVerifierTransport,
            "_run_container",
            autospec=True,
        ) as run:
            with self.assertRaisesRegex(DockerVerifierError, "identity changed"):
                transport.execute(request)
            run.assert_not_called()

    def test_asset_mount_is_an_exact_read_only_view_and_image_id_is_executed(
        self,
    ) -> None:
        transport, request, _ = self.asset_request()
        expected = _result(request)

        self.execute_with(
            _completed(stdout=json.dumps(expected.to_dict())),
            transport=transport,
            request=request,
        )

        self.assertEqual(self.observed_asset_names, ("check.py", "fixture.txt"))
        rendered = " ".join(self.observed_command)
        self.assertIn("dst=/verifier-assets,readonly", rendered)
        self.assertNotIn("dst=/artifacts", rendered)
        self.assertNotIn("dst=/request.json", rendered)
        self.assertNotIn("dst=/bundles", rendered)
        self.assertIn(request.execution_identity.image_id, self.observed_command)

    def test_missing_asset_store_and_reference_substitution_fail_before_run(
        self,
    ) -> None:
        transport, request, reference = self.asset_request()
        cases = (
            (
                replace(transport, asset_store=None),
                request,
                "requires an asset bundle store",
            ),
            (
                transport,
                replace(
                    request,
                    profile=replace(
                        request.profile,
                        asset_bundle=replace(reference, tree_hash=_digest("9")),
                    ),
                ),
                "isolated-view validation",
            ),
        )
        for candidate_transport, candidate_request, expected in cases:
            with self.subTest(expected=expected), patch.object(
                DockerVerifierTransport,
                "_run_container",
                autospec=True,
            ) as run:
                with self.assertRaisesRegex(DockerVerifierError, expected):
                    candidate_transport.execute(candidate_request)
                run.assert_not_called()

    def test_symlinked_asset_object_fails_before_container_start(self) -> None:
        transport, request, reference = self.asset_request()
        digest = reference.bundle_id.rsplit(":", 1)[-1]
        stored = transport.asset_store / digest / "files" / "check.py"
        outside = self.root / "outside.py"
        outside.write_text("print('substituted')\n", encoding="utf-8")
        stored.unlink()
        stored.symlink_to(outside)

        with patch.object(
            DockerVerifierTransport,
            "_run_container",
            autospec=True,
        ) as run:
            with self.assertRaisesRegex(DockerVerifierError, "isolated-view validation"):
                transport.execute(request)
            run.assert_not_called()

    def test_command_mount_is_a_fresh_exact_workspace_only_view(self) -> None:
        (self.transport.bundle_store / ("f" * 64 + ".tar")).write_bytes(b"decoy")
        (self.transport.bundle_store / ("f" * 64 + ".json")).write_text(
            "{}",
            encoding="utf-8",
        )
        expected = _result(self.request)

        self.execute_with(_completed(stdout=json.dumps(expected.to_dict())))

        self.assertEqual(self.observed_bundle_names, ("tracked.txt",))
        self.assertIsNotNone(self.observed_bundle_source)
        self.assertNotEqual(
            self.observed_bundle_source,
            self.transport.bundle_store,
        )
        rendered = " ".join(self.observed_command)
        self.assertIn("dst=/workspace", rendered)
        self.assertNotIn("dst=/bundles", rendered)
        self.assertNotIn("dst=/request.json", rendered)
        self.assertNotIn("dst=/artifacts", rendered)

    def test_bundle_archive_symlink_and_digest_tampering_fail_before_run(self) -> None:
        digest = self.request.workspace_bundle.archive_sha256.removeprefix("sha256:")
        archive = self.transport.bundle_store / f"{digest}.tar"
        original = archive.read_bytes()
        cases = ("symlink", "digest")
        for case in cases:
            with self.subTest(case=case):
                archive.unlink(missing_ok=True)
                if case == "symlink":
                    target = self.transport.bundle_store / "original.tar"
                    target.write_bytes(original)
                    archive.symlink_to(target.name)
                else:
                    tampered = bytearray(original)
                    tampered[0] ^= 1
                    archive.write_bytes(tampered)
                with patch.object(
                    DockerVerifierTransport,
                    "_run_container",
                    autospec=True,
                ) as run:
                    with self.assertRaisesRegex(
                        DockerVerifierError,
                        "isolated-view validation",
                    ):
                        self.transport.execute(self.request)
                    run.assert_not_called()
                archive.unlink(missing_ok=True)

    def test_bundle_reference_must_match_request_before_container_start(self) -> None:
        digest = self.request.workspace_bundle.archive_sha256.removeprefix("sha256:")
        reference_path = self.transport.bundle_store / f"{digest}.json"
        different = replace(
            self.request.workspace_bundle,
            source_commit_sha="9" * 40,
        )
        reference_path.write_text(
            json.dumps(different.to_dict()),
            encoding="utf-8",
        )

        with patch.object(
            DockerVerifierTransport,
            "_run_container",
            autospec=True,
        ) as run:
            with self.assertRaisesRegex(
                DockerVerifierError,
                "does not match the request",
            ):
                self.transport.execute(self.request)
            run.assert_not_called()

    def test_bundle_source_stat_change_fails_before_container_start(self) -> None:
        with patch(
            "sisyphus_harness.adapters.docker_verifier._same_stable_file",
            side_effect=(True, False),
        ), patch.object(
            DockerVerifierTransport,
            "_run_container",
            autospec=True,
        ) as run:
            with self.assertRaisesRegex(
                DockerVerifierError,
                "isolated-view validation",
            ):
                self.transport.execute(self.request)
            run.assert_not_called()

    def test_bounded_capture_preserves_output_and_enforces_timeout(self) -> None:
        transport = replace(
            self.transport,
            timeout_seconds=2,
            max_output_bytes=128,
        )
        completed = transport._run_container(
            [
                sys.executable,
                "-c",
                "import os; os.write(1, b'out'); os.write(2, b'err')",
            ]
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "out")
        self.assertEqual(completed.stderr, "err")

        timeout_transport = replace(transport, timeout_seconds=0.05)
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            timeout_transport._run_container(
                [sys.executable, "-c", "import time; time.sleep(30)"]
            )
        self.assertLess(time.monotonic() - started, 2)

    def test_combined_container_output_limit_kills_and_removes_container(self) -> None:
        transport = replace(
            self.transport,
            timeout_seconds=5,
            max_output_bytes=1024,
        )
        cleanup_calls: list[tuple[str, ...]] = []

        def command(
            _: DockerVerifierTransport,
            __: CommandSpec,
            *,
            workspace: Path,
            cidfile: Path,
            asset_view: Path | None,
            execution_identity: VerifierExecutionIdentity,
        ) -> list[str]:
            del workspace, asset_view, execution_identity
            program = (
                "from pathlib import Path; import os,time; "
                f"Path({str(cidfile)!r}).write_text({'a' * 64!r}); "
                "os.write(1, b'x' * 700); os.write(2, b'y' * 700); time.sleep(30)"
            )
            return [sys.executable, "-c", program]

        def cleanup(
            command: list[str] | tuple[str, ...],
            **_: object,
        ) -> SimpleNamespace:
            cleanup_calls.append(tuple(command))
            return _completed(stdout="")

        started = time.monotonic()
        with patch.object(
            DockerVerifierTransport,
            "command",
            autospec=True,
            side_effect=command,
        ), patch(
            "sisyphus_harness.adapters.docker_verifier.subprocess.run",
            side_effect=cleanup,
        ):
            capture = transport._capture_command(
                self.request.profile.commands[0],
                workspace=self.root / "repository",
                asset_view=None,
                cidfile=self.root / "limited.cid",
                execution_identity=self.request.execution_identity,
                timeout_seconds=5,
            )

        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(capture.output_limited)
        self.assertEqual(
            capture.stdout.count("x") + capture.stderr.count("y"),
            transport.max_output_bytes,
        )
        self.assertIn("output exceeded limit", capture.stderr)
        self.assertIn(("docker", "rm", "--force", "a" * 64), cleanup_calls)
        self.assertFalse((transport.artifact_root / self.request.run_id).exists())

    def test_runtime_start_failure_is_closed_and_timeout_is_evidence(self) -> None:
        unavailable = OSError("docker unavailable")
        with patch.object(
            DockerVerifierTransport,
            "_run_container",
            autospec=True,
            side_effect=unavailable,
        ):
            with self.assertRaisesRegex(
                DockerVerifierError,
                "executable probe could not start",
            ) as raised:
                self.transport.execute(self.request)
        self.assertIs(raised.exception.__cause__, unavailable)

        cidfile = self.root / "timeout.cid"
        timeout = subprocess.TimeoutExpired(
            ("docker", "run"),
            0.1,
            output=b"partial-out",
            stderr=b"partial-err",
        )
        with patch.object(
            DockerVerifierTransport,
            "_run_container",
            autospec=True,
            side_effect=timeout,
        ), patch.object(
            DockerVerifierTransport,
            "_remove_container",
            autospec=True,
        ) as remove:
            capture = self.transport._capture_command(
                self.request.profile.commands[0],
                workspace=self.root / "repository",
                asset_view=None,
                cidfile=cidfile,
                execution_identity=self.request.execution_identity,
                timeout_seconds=0.1,
            )

        self.assertTrue(capture.timed_out)
        self.assertIsNone(capture.returncode)
        self.assertEqual(capture.stdout, "partial-out")
        self.assertIn("partial-err", capture.stderr)
        self.assertIn("timed out", capture.stderr)
        remove.assert_called_once_with(cidfile)

    def test_timeout_forcibly_removes_the_container_recorded_by_cidfile(self) -> None:
        calls: list[tuple[str, ...]] = []

        def run_container(
            _: DockerVerifierTransport,
            command: list[str],
        ) -> object:
            rendered = tuple(command)
            cidfile = Path(rendered[rendered.index("--cidfile") + 1])
            cidfile.write_text("a" * 64 + "\n", encoding="ascii")
            raise subprocess.TimeoutExpired(rendered, 0.1)

        def run_cleanup(command: list[str] | tuple[str, ...], **_: object) -> object:
            rendered = tuple(command)
            calls.append(rendered)
            return _completed(stdout="", returncode=0)

        with patch.object(
            DockerVerifierTransport,
            "_run_container",
            autospec=True,
            side_effect=run_container,
        ), patch(
            "sisyphus_harness.adapters.docker_verifier.subprocess.run",
            side_effect=run_cleanup,
        ):
            capture = self.transport._capture_command(
                self.request.profile.commands[0],
                workspace=self.root / "repository",
                asset_view=None,
                cidfile=self.root / "timeout-cleanup.cid",
                execution_identity=self.request.execution_identity,
                timeout_seconds=0.1,
            )

        self.assertTrue(capture.timed_out)
        self.assertIn(("docker", "rm", "--force", "a" * 64), calls)

    def test_receipt_is_host_validated_before_atomic_publication(self) -> None:
        with patch.object(
            FilesystemVerificationEvidenceStore,
            "read_receipt",
            autospec=True,
            side_effect=VerificationEvidenceError("invalid receipt"),
        ):
            with self.assertRaisesRegex(
                DockerVerifierError,
                "artifact failed host validation",
            ):
                self.execute_with(_completed(stdout="candidate diagnostic"))

        self.assertFalse((self.transport.artifact_root / self.request.run_id).exists())

    def test_inline_staged_and_published_receipts_must_be_identical(self) -> None:
        expected = _result(self.request)
        different = replace(expected.receipt, workspace="/different-workspace")
        with patch.object(
            FilesystemVerificationEvidenceStore,
            "read_receipt",
            autospec=True,
            return_value=different,
        ):
            with self.assertRaisesRegex(DockerVerifierError, "does not match.*artifact"):
                self.execute_with(_completed(stdout="candidate diagnostic"))

        with patch.object(
            DockerVerifierTransport,
            "read_receipt",
            autospec=True,
            return_value=different,
        ):
            with self.assertRaisesRegex(DockerVerifierError, "published receipt"):
                self.execute_with(_completed(stdout="candidate diagnostic"))

    def test_publication_lock_and_destination_collisions_fail_closed(self) -> None:
        self.transport.artifact_root.mkdir(parents=True, exist_ok=True)
        staging = self.root / "publish-staging"
        staging.mkdir()
        with self.assertRaisesRegex(DockerVerifierError, "regular run directory"):
            self.transport._publish_run(staging, self.request)

        source = staging / self.request.run_id
        source.mkdir()
        lock = self.transport.artifact_root / f".{self.request.run_id}.publish.lock"
        lock.write_text("locked", encoding="utf-8")
        with self.assertRaisesRegex(DockerVerifierError, "already being published"):
            self.transport._publish_run(staging, self.request)
        lock.unlink()

        destination = self.transport.artifact_root / self.request.run_id
        destination.mkdir()
        with self.assertRaisesRegex(DockerVerifierError, "already exists"):
            self.transport._publish_run(staging, self.request)

    def test_publication_commits_request_first_and_rolls_it_back_on_failure(
        self,
    ) -> None:
        self.transport.artifact_root.mkdir(parents=True)
        staging = self.root / "ordered-publish-staging"
        source = staging / self.request.run_id
        source.mkdir(parents=True)
        (source / "receipt.json").write_text("staged", encoding="utf-8")
        events: list[str] = []

        def write_request(path: Path, _: object) -> None:
            events.append("request")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("request", encoding="utf-8")

        def fail_run_commit(_: object, __: object) -> None:
            events.append("run")
            raise OSError("rename failed")

        with patch(
            "sisyphus_harness.adapters.docker_verifier.write_json_atomic",
            side_effect=write_request,
        ), patch(
            "sisyphus_harness.adapters.docker_verifier.os.replace",
            side_effect=fail_run_commit,
        ):
            with self.assertRaisesRegex(DockerVerifierError, "could not be published"):
                self.transport._publish_run(staging, self.request)

        self.assertEqual(events, ["request", "run"])
        self.assertTrue(source.is_dir())
        self.assertFalse(
            (self.transport.artifact_root / self.request.run_id).exists()
        )
        self.assertFalse(
            (
                self.transport.artifact_root
                / "service-requests"
                / f"{self.request.run_id}.json"
            ).exists()
        )

    def test_container_cleanup_rejects_untrusted_ids_and_ignores_runtime_errors(
        self,
    ) -> None:
        cidfile = self.root / "cleanup.cid"
        for content in ("x" * 129, "not-a-container-id"):
            with self.subTest(content=content[:16]), patch(
                "sisyphus_harness.adapters.docker_verifier.subprocess.run",
            ) as cleanup:
                cidfile.write_text(content, encoding="ascii")
                self.transport._remove_container(cidfile)
                cleanup.assert_not_called()

        cidfile.write_text("a" * 64, encoding="ascii")
        with patch(
            "sisyphus_harness.adapters.docker_verifier.subprocess.run",
            side_effect=OSError("docker unavailable"),
        ) as cleanup:
            self.transport._remove_container(cidfile)
            cleanup.assert_called_once()

    def test_non_protocol_exit_codes_include_bounded_stderr_or_fallback(self) -> None:
        cases = (
            ("prefix-" + "x" * 2100, "x" * 2000),
            ("   ", "Docker could not start the verifier command container"),
        )
        for stderr, expected in cases:
            with self.subTest(has_detail=bool(stderr.strip())):
                with self.assertRaisesRegex(DockerVerifierError, expected):
                    self.execute_with(
                        _completed(stdout="ignored", returncode=125, stderr=stderr)
                    )

    def test_candidate_output_is_diagnostic_not_a_service_protocol(self) -> None:
        outputs = (
            "\n  \n",
            "service log\nnot-json\n",
            '{"value": 1, "value": 2}\n',
        )
        for index, stdout in enumerate(outputs):
            request = replace(self.request, run_id=f"diagnostic-output-{index}")
            with self.subTest(index=index):
                result = self.execute_with(
                    _completed(stdout=stdout),
                    request=request,
                )
                command = result.receipt.commands[0]
                artifact = (
                    self.transport.artifact_root
                    / request.run_id
                    / command.stdout_path
                )
                self.assertTrue(result.receipt.passed)
                self.assertEqual(artifact.read_text(encoding="utf-8"), stdout)

    def test_result_must_bind_request_bundle_and_profile(self) -> None:
        other_request = _request(profile=_profile("other-profile"))
        other_run_receipt = replace(_receipt(self.request), run_id="other-run")
        other_run_result = VerificationServiceResult(
            request_digest=self.request.request_digest,
            workspace_bundle_id=self.request.workspace_bundle.bundle_id,
            profile_digest=self.request.profile.profile_digest,
            receipt=other_run_receipt,
            receipt_artifact=ArtifactRef(
                artifact_id="other-run/receipt.json",
                sha256=_digest("5"),
                size_bytes=1,
                media_type=VERIFICATION_RECEIPT_MEDIA_TYPE,
            ),
            execution_identity=self.request.execution_identity,
            schema_version="sisyphus_harness.verification_service_result.v2",
        )
        cases = (
            (
                _result(other_request),
                "different request",
            ),
            (
                _result(
                    self.request,
                    workspace_bundle_id=_bundle("8").bundle_id,
                ),
                "bindings are inconsistent",
            ),
            (
                _result(self.request, profile_digest=_digest("7")),
                "bindings are inconsistent",
            ),
            (other_run_result, "different run"),
        )
        for result, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(DockerVerifierError, expected):
                    self.transport._parse_result(
                        _completed(stdout=json.dumps(result.to_dict())),
                        self.request,
                    )

    def test_result_with_substituted_command_fails_before_publication(self) -> None:
        result = _result(self.request)
        command = replace(
            result.receipt.commands[0],
            argv=(sys.executable, "-c", "raise SystemExit(0)"),
        )
        receipt = replace(result.receipt, commands=(command,))
        object.__setattr__(result, "receipt", receipt)

        with self.assertRaisesRegex(DockerVerifierError, "does not match the profile"):
            self.transport._parse_result(
                _completed(stdout=json.dumps(result.to_dict())),
                self.request,
            )
        self.assertFalse(
            (self.transport.artifact_root / self.request.run_id).exists()
        )

    def test_nonzero_candidate_exit_creates_failed_host_receipt(self) -> None:
        result = self.execute_with(
            _completed(
                stdout="diagnostic line\n",
                returncode=1,
                stderr="verification failed",
            )
        )

        command = result.receipt.commands[0]
        run_directory = self.transport.artifact_root / self.request.run_id
        self.assertFalse(result.receipt.passed)
        self.assertFalse(command.passed)
        self.assertEqual(command.exit_code, 1)
        self.assertEqual(command.failure_category, "command_failure")
        self.assertEqual(
            (run_directory / command.stdout_path).read_text(encoding="utf-8"),
            "diagnostic line\n",
        )
        self.assertEqual(
            (run_directory / command.stderr_path).read_text(encoding="utf-8"),
            "verification failed",
        )
        self.assertEqual(self.transport.read_receipt(result.receipt_artifact), result.receipt)
        self.assertTrue(
            (
                self.transport.artifact_root
                / "service-requests"
                / f"{self.request.run_id}.json"
            ).is_file()
        )


class BundleVerifierServiceEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.artifacts = self.root / "artifacts"
        self.work = self.root / "work"

    def service(self, store: object) -> BundleVerifierService:
        return BundleVerifierService(
            bundle_store=store,  # type: ignore[arg-type]
            artifact_root=self.artifacts,
            work_root=self.work,
        )

    def asset_case(
        self,
    ) -> tuple[
        FilesystemWorkspaceBundleStore,
        BundleVerificationRequest,
        Path,
    ]:
        repository = create_git_repo(self.root / "asset-repository")
        workspace_store = FilesystemWorkspaceBundleStore(self.root / "asset-bundles")
        bundle = workspace_store.create(repository)
        source = self.root / "service-asset-source"
        source.mkdir()
        (source / "check.py").write_text("print('asset check')\n", encoding="utf-8")
        asset_store = FilesystemVerifierAssetBundleStore(
            self.root / "service-asset-store"
        )
        asset = asset_store.create(source)
        asset_root = self.root / "service-assets"
        asset_store.materialize(asset, asset_root)
        profile = VerificationProfile(
            profile_id="service-assets",
            commands=(
                CommandSpec(
                    name="asset-check",
                    argv=(sys.executable, str(asset_root / "check.py")),
                    timeout_seconds=2,
                    criteria=("asset command completes",),
                ),
            ),
            asset_bundle=asset,
            schema_version="sisyphus_harness.verification_profile.v2",
        )
        return (
            workspace_store,
            _request(run_id="service-assets", bundle=bundle, profile=profile),
            asset_root,
        )

    def test_stored_reference_must_equal_request_authority(self) -> None:
        request = _request()
        store = Mock()
        store.load.return_value = _bundle("8")

        with self.assertRaisesRegex(
            VerifierServiceError,
            "does not match stored authority",
        ):
            self.service(store).execute(request)

        store.materialize.assert_not_called()
        self.assertFalse(self.work.exists())

    def test_materialization_hash_mismatch_is_rejected_and_attempt_is_cleaned(self) -> None:
        request = _request()
        store = Mock()
        store.load.return_value = request.workspace_bundle
        store.materialize.return_value = _digest("8")

        with self.assertRaisesRegex(
            VerifierServiceError,
            "does not match its bundle tree",
        ):
            self.service(store).execute(request)

        self.assertTrue(self.work.is_dir())
        self.assertEqual(list(self.work.iterdir()), [])

    def test_real_service_materializes_verifies_and_removes_attempt(self) -> None:
        repository = create_git_repo(self.root / "repository")
        store = FilesystemWorkspaceBundleStore(self.root / "bundles")
        bundle = store.create(repository)
        request = _request(run_id="real-service", bundle=bundle)

        result = self.service(store).execute(request)

        self.assertTrue(result.receipt.passed)
        self.assertEqual(result.workspace_bundle_id, bundle.bundle_id)
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertTrue(
            (self.artifacts / "service-requests" / "real-service.json").is_file()
        )

    def test_service_requires_exactly_the_asset_mount_requested_by_profile(self) -> None:
        store, asset_request, asset_root = self.asset_case()
        with self.assertRaisesRegex(VerifierServiceError, "requires.*asset mount"):
            self.service(store).execute(asset_request)

        no_asset_request = _request(
            run_id="unrequested-assets",
            bundle=asset_request.workspace_bundle,
        )
        with self.assertRaisesRegex(VerifierServiceError, "unrequested.*forbidden"):
            BundleVerifierService(
                bundle_store=store,
                artifact_root=self.artifacts,
                work_root=self.work,
                asset_root=asset_root,
            ).execute(no_asset_request)

    def test_service_binds_asset_tree_into_v3_receipt(self) -> None:
        store, request, asset_root = self.asset_case()
        result = BundleVerifierService(
            bundle_store=store,
            artifact_root=self.artifacts,
            work_root=self.work,
            asset_root=asset_root,
        ).execute(request)

        self.assertTrue(result.receipt.passed)
        self.assertEqual(result.receipt.schema_version, "sisyphus_harness.verification.v3")
        self.assertEqual(
            result.receipt.verifier_asset_bundle_id,
            request.profile.asset_bundle.bundle_id,
        )

    def test_service_rejects_asset_mutation_before_or_during_execution(self) -> None:
        store, request, asset_root = self.asset_case()
        check = asset_root / "check.py"
        check.chmod(0o644)
        check.write_text("print('substituted')\n", encoding="utf-8")
        service = BundleVerifierService(
            bundle_store=store,
            artifact_root=self.artifacts,
            work_root=self.work,
            asset_root=asset_root,
        )
        with patch(
            "sisyphus_harness.services.verifier.BoundedVerifier.verify",
        ) as verify:
            with self.assertRaisesRegex(VerifierServiceError, "does not match"):
                service.execute(request)
            verify.assert_not_called()

        asset_root.chmod(0o755)
        check.chmod(0o644)
        check.write_text("print('asset check')\n", encoding="utf-8")
        check.chmod(0o444)
        asset_root.chmod(0o555)
        with patch(
            "sisyphus_harness.services.verifier.verifier_asset_tree_hash",
            side_effect=(
                request.profile.asset_bundle.tree_hash,
                _digest("9"),
            ),
        ):
            with self.assertRaisesRegex(VerifierServiceError, "changed"):
                service.execute(replace(request, run_id="asset-change-during-run"))


class VerifierServiceMainEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.request = _request(run_id="main-run")
        self.request_path = self.root / "request.json"
        self.request_path.write_text(
            json.dumps(self.request.to_dict()),
            encoding="utf-8",
        )

    def argv(self, *, result: Path | None = None) -> list[str]:
        argv = [
            "--request",
            str(self.request_path),
            "--bundle-store",
            str(self.root / "bundles"),
            "--artifact-root",
            str(self.root / "artifacts"),
            "--work-root",
            str(self.root / "work"),
        ]
        if result is not None:
            argv.extend(("--result", str(result)))
        return argv

    def test_parser_maps_all_paths(self) -> None:
        result_path = self.root / "result.json"

        parsed = _parser().parse_args(self.argv(result=result_path))

        self.assertEqual(parsed.request, self.request_path)
        self.assertEqual(parsed.bundle_store, self.root / "bundles")
        self.assertEqual(parsed.artifact_root, self.root / "artifacts")
        self.assertEqual(parsed.work_root, self.root / "work")
        self.assertEqual(parsed.result, result_path)

    def test_main_writes_passing_result_and_returns_zero(self) -> None:
        expected = _result(self.request)
        result_path = self.root / "nested" / "result.json"
        stdout = io.StringIO()

        with patch(
            "sisyphus_harness.services.verifier.BundleVerifierService.execute",
            return_value=expected,
        ), redirect_stdout(stdout):
            code = main(self.argv(result=result_path))

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), expected.to_dict())
        self.assertEqual(
            json.loads(result_path.read_text(encoding="utf-8")),
            expected.to_dict(),
        )

    def test_main_returns_one_for_a_valid_failing_receipt_without_result_path(self) -> None:
        expected = _result(self.request, passed=False)
        stdout = io.StringIO()

        with patch(
            "sisyphus_harness.services.verifier.BundleVerifierService.execute",
            return_value=expected,
        ), redirect_stdout(stdout):
            code = main(self.argv())

        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout.getvalue()), expected.to_dict())

    def test_main_reports_invalid_and_oversized_requests_as_service_errors(self) -> None:
        cases = (
            (b"not-json", "invalid JSON"),
            (b"x" * (4 * 1024 * 1024 + 1), "exceeds byte limit"),
        )
        for content, expected in cases:
            with self.subTest(expected=expected):
                self.request_path.write_bytes(content)
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    code = main(self.argv())
                payload = json.loads(stderr.getvalue())
                self.assertEqual(code, 2)
                self.assertEqual(
                    payload["schema_version"],
                    "sisyphus_harness.service_error.v1",
                )
                self.assertIn(expected, payload["error"])


class VerificationServiceContractEdgeTests(unittest.TestCase):
    def test_execution_identity_is_strict_and_content_bound(self) -> None:
        identity = _identity()
        self.assertEqual(
            VerifierExecutionIdentity.from_dict(identity.to_dict()),
            identity,
        )
        unknown = identity.to_dict()
        unknown["unexpected"] = True
        tampered = identity.to_dict()
        tampered["image_id"] = _digest("f")
        malformed = identity.to_dict()
        malformed["image_id"] = "verifier:test"

        for payload, expected in (
            (unknown, "unknown fields"),
            (tampered, "digest does not match content"),
            (malformed, "must be SHA-256"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, expected):
                    VerifierExecutionIdentity.from_dict(payload)

    def test_profile_rejects_unsafe_empty_duplicate_and_wrong_schema_values(self) -> None:
        command = _profile().commands[0]
        cases = (
            lambda: VerificationProfile(profile_id="../unsafe", commands=(command,)),
            lambda: VerificationProfile(profile_id="empty", commands=()),
            lambda: VerificationProfile(
                profile_id="duplicate",
                commands=(command, command),
            ),
            lambda: VerificationProfile(
                profile_id="schema",
                commands=(command,),
                schema_version="future",
            ),
        )
        for construct in cases:
            with self.subTest(case=construct):
                with self.assertRaises(ValueError):
                    construct()

    def test_profile_wire_contract_is_strict_and_content_bound(self) -> None:
        profile = _profile()
        non_list = profile.to_dict()
        non_list["commands"] = "not-a-list"
        tampered = profile.to_dict()
        tampered["profile_id"] = "tampered"
        unknown = profile.to_dict()
        unknown["unexpected"] = True

        for payload, expected in (
            (non_list, "commands must be a list"),
            (tampered, "digest does not match content"),
            (unknown, "unknown fields"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, expected):
                    VerificationProfile.from_dict(payload)

    def test_request_rejects_unsafe_id_schema_unknown_fields_and_tampering(self) -> None:
        profile = _profile()
        bundle = _bundle()
        with self.assertRaisesRegex(ValueError, "unsafe"):
            BundleVerificationRequest(
                run_id="..",
                workspace_bundle=bundle,
                profile=profile,
            )
        with self.assertRaisesRegex(ValueError, "unsupported"):
            BundleVerificationRequest(
                run_id="schema",
                workspace_bundle=bundle,
                profile=profile,
                schema_version="future",
            )

        request = _request()
        unknown = request.to_dict()
        unknown["unexpected"] = True
        tampered = request.to_dict()
        tampered["run_id"] = "different"
        invalid_digest = request.to_dict()
        invalid_digest["request_digest"] = True
        for payload, expected in (
            (unknown, "unknown fields"),
            (tampered, "digest does not match content"),
            (invalid_digest, "non-empty string"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, expected):
                    BundleVerificationRequest.from_dict(payload)

    def test_result_constructor_rejects_unbound_or_unsafe_contracts(self) -> None:
        request = _request()
        receipt = _receipt(request)
        artifact = _result(request).receipt_artifact
        other_receipt = _receipt(_request(profile=_profile("other")))
        cases = (
            (
                dict(
                    request_digest="bad",
                    profile_digest=request.profile.profile_digest,
                    receipt=receipt,
                    receipt_artifact=artifact,
                ),
                "must be SHA-256",
            ),
            (
                dict(
                    request_digest=request.request_digest,
                    profile_digest="bad",
                    receipt=receipt,
                    receipt_artifact=artifact,
                ),
                "must be SHA-256",
            ),
            (
                dict(
                    request_digest=request.request_digest,
                    profile_digest=request.profile.profile_digest,
                    receipt=other_receipt,
                    receipt_artifact=artifact,
                ),
                "not bound",
            ),
            (
                dict(
                    request_digest=request.request_digest,
                    profile_digest=request.profile.profile_digest,
                    receipt=receipt,
                    receipt_artifact=replace(
                        artifact,
                        artifact_id="other/receipt.json",
                    ),
                ),
                "artifact ID is inconsistent",
            ),
        )
        for arguments, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, expected):
                    VerificationServiceResult(
                        workspace_bundle_id=request.workspace_bundle.bundle_id,
                        **arguments,
                    )

        with self.assertRaisesRegex(ValueError, "unsupported"):
            replace(_result(request), schema_version="future")

        inconsistent = replace(_result(request).receipt)
        object.__setattr__(inconsistent, "execution_identity_digest", _digest("f"))
        with self.assertRaisesRegex(ValueError, "bindings are inconsistent"):
            replace(_result(request), receipt=inconsistent)

    def test_result_wire_contract_rejects_unknown_and_invalid_string_fields(self) -> None:
        payload = _result(_request()).to_dict()
        unknown = dict(payload, unexpected=True)
        empty_bundle = dict(payload, workspace_bundle_id="")
        for candidate, expected in (
            (unknown, "unknown fields"),
            (empty_bundle, "non-empty string"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, expected):
                    VerificationServiceResult.from_dict(candidate)


class VerificationEvidenceStoreEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name) / "evidence"
        self.root.mkdir()
        self.request = _request(run_id="stored-run")
        self.receipt = _receipt(self.request)

    def write_receipt(
        self,
        receipt: VerificationReceipt | None = None,
        *,
        path_run_id: str | None = None,
        content: bytes | None = None,
    ) -> Path:
        path = self.root / (path_run_id or self.request.run_id) / "receipt.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            content
            if content is not None
            else (json.dumps((receipt or self.receipt).to_dict()) + "\n").encode()
        )
        return path

    def test_constructor_and_run_ids_fail_closed(self) -> None:
        for limit in (True, 0, -1, 1.5):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(ValueError, "must be positive"):
                    FilesystemVerificationEvidenceStore(
                        self.root,
                        max_receipt_bytes=limit,  # type: ignore[arg-type]
                    )

        store = FilesystemVerificationEvidenceStore(self.root)
        for run_id in ("..", "with/slash", ""):
            with self.subTest(run_id=run_id):
                with self.assertRaisesRegex(
                    VerificationEvidenceError,
                    "unsafe characters",
                ):
                    store.receipt_reference(run_id)

    def test_reference_and_read_round_trip_with_digest_binding(self) -> None:
        self.write_receipt()
        store = FilesystemVerificationEvidenceStore(self.root)

        reference = store.receipt_reference(self.request.run_id)
        loaded = store.read_receipt(reference)

        self.assertEqual(loaded, self.receipt)
        self.assertEqual(
            reference.size_bytes,
            (self.root / reference.artifact_id).stat().st_size,
        )
        self.assertEqual(reference.media_type, VERIFICATION_RECEIPT_MEDIA_TYPE)

    def test_reference_rejects_run_mismatch_invalid_json_and_oversize(self) -> None:
        other = _receipt(_request(run_id="other-run"))
        store = FilesystemVerificationEvidenceStore(self.root)

        self.write_receipt(other)
        with self.assertRaisesRegex(VerificationEvidenceError, "run ID does not match"):
            store.receipt_reference(self.request.run_id)

        self.write_receipt(content=b"not-json")
        with self.assertRaisesRegex(VerificationEvidenceError, "invalid JSON"):
            store.receipt_reference(self.request.run_id)

        receipt_path = self.write_receipt()
        limited = FilesystemVerificationEvidenceStore(
            self.root,
            max_receipt_bytes=receipt_path.stat().st_size - 1,
        )
        with self.assertRaisesRegex(VerificationEvidenceError, "byte limit"):
            limited.receipt_reference(self.request.run_id)

    def test_read_rejects_media_size_digest_and_artifact_path_mismatch(self) -> None:
        path = self.write_receipt()
        store = FilesystemVerificationEvidenceStore(self.root)
        reference = store.receipt_reference(self.request.run_id)

        cases = (
            (
                replace(reference, media_type="application/json"),
                "not a verification receipt",
            ),
            (replace(reference, size_bytes=reference.size_bytes + 1), "size does not match"),
            (replace(reference, sha256=_digest("8")), "digest does not match"),
        )
        for candidate, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(VerificationEvidenceError, expected):
                    store.read_receipt(candidate)

        alias_path = self.root / "alias" / "receipt.json"
        alias_path.parent.mkdir()
        alias_path.write_bytes(path.read_bytes())
        alias = ArtifactRef(
            artifact_id="alias/receipt.json",
            sha256=reference.sha256,
            size_bytes=reference.size_bytes,
            media_type=VERIFICATION_RECEIPT_MEDIA_TYPE,
        )
        with self.assertRaisesRegex(VerificationEvidenceError, "run ID does not match"):
            store.read_receipt(alias)

    def test_unavailable_missing_and_non_regular_paths_are_normalized(self) -> None:
        unavailable = FilesystemVerificationEvidenceStore(self.root / "missing-root")
        with self.assertRaisesRegex(VerificationEvidenceError, "root is unavailable"):
            unavailable.receipt_reference("run")

        store = FilesystemVerificationEvidenceStore(self.root)
        with self.assertRaisesRegex(VerificationEvidenceError, "artifact ID is empty"):
            store._read_bytes("")
        with self.assertRaisesRegex(VerificationEvidenceError, "cannot be read"):
            store._read_bytes("missing/receipt.json")

        receipt_directory = self.root / "directory-run" / "receipt.json"
        receipt_directory.mkdir(parents=True)
        with self.assertRaisesRegex(VerificationEvidenceError, "regular file"):
            store.receipt_reference("directory-run")

    def test_symlink_and_mid_read_change_are_rejected(self) -> None:
        receipt_path = self.write_receipt()
        outside = Path(self.temporary_directory.name) / "outside.json"
        outside.write_bytes(receipt_path.read_bytes())
        receipt_path.unlink()
        receipt_path.symlink_to(outside)
        store = FilesystemVerificationEvidenceStore(self.root)
        with self.assertRaisesRegex(VerificationEvidenceError, "cannot be read"):
            store.receipt_reference(self.request.run_id)

        receipt_path.unlink()
        self.write_receipt()
        real_fstat = os.fstat
        calls = 0

        def changing_fstat(descriptor: int) -> os.stat_result | SimpleNamespace:
            nonlocal calls
            value = real_fstat(descriptor)
            calls += 1
            if calls == 2:
                return SimpleNamespace(
                    st_dev=value.st_dev,
                    st_ino=value.st_ino,
                    st_size=value.st_size,
                    st_mtime_ns=value.st_mtime_ns + 1,
                    st_ctime_ns=value.st_ctime_ns,
                )
            return value

        with patch(
            "sisyphus_harness.infra.verification_evidence.os.fstat",
            side_effect=changing_fstat,
        ):
            with self.assertRaisesRegex(VerificationEvidenceError, "changed while"):
                store.receipt_reference(self.request.run_id)

    def test_limit_is_rechecked_against_bytes_read_after_open(self) -> None:
        self.write_receipt(content=b"x" * 11)
        store = FilesystemVerificationEvidenceStore(
            self.root,
            max_receipt_bytes=10,
        )
        real_fstat = os.fstat

        def understated_fstat(descriptor: int) -> SimpleNamespace:
            value = real_fstat(descriptor)
            return SimpleNamespace(
                st_mode=value.st_mode,
                st_dev=value.st_dev,
                st_ino=value.st_ino,
                st_size=10,
                st_mtime_ns=value.st_mtime_ns,
                st_ctime_ns=value.st_ctime_ns,
            )

        with patch(
            "sisyphus_harness.infra.verification_evidence.os.fstat",
            side_effect=understated_fstat,
        ):
            with self.assertRaisesRegex(VerificationEvidenceError, "byte limit"):
                store.receipt_reference(self.request.run_id)


if __name__ == "__main__":
    unittest.main()
