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
)
from sisyphus_harness.contracts.artifacts import ArtifactRef
from sisyphus_harness.contracts.verification import CommandSpec, VerificationReceipt
from sisyphus_harness.contracts.verification_service import (
    BundleVerificationRequest,
    VerificationProfile,
    VerificationServiceResult,
)
from sisyphus_harness.contracts.workspace import WorkspaceBundleRef
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
    )


def _receipt(
    request: BundleVerificationRequest,
    *,
    passed: bool = True,
    run_id: str | None = None,
) -> VerificationReceipt:
    unchanged = passed
    return VerificationReceipt(
        run_id=run_id or request.run_id,
        workspace="/workspace",
        worktree_commit_sha=request.workspace_bundle.source_commit_sha,
        started_at="2026-07-20T00:00:00Z",
        finished_at="2026-07-20T00:00:01Z",
        passed=passed,
        commands=(),
        workspace_state_before=request.workspace_bundle.tree_hash,
        workspace_state_after=(
            request.workspace_bundle.tree_hash if unchanged else _digest("9")
        ),
        workspace_unchanged=unchanged,
        request_digest=request.request_digest,
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
    return VerificationServiceResult(
        request_digest=request.request_digest,
        workspace_bundle_id=(
            workspace_bundle_id or request.workspace_bundle.bundle_id
        ),
        profile_digest=profile_digest or request.profile.profile_digest,
        receipt=receipt,
        receipt_artifact=ArtifactRef(
            artifact_id=f"{request.run_id}/receipt.json",
            sha256=f"sha256:{hashlib.sha256(receipt_content).hexdigest()}",
            size_bytes=len(receipt_content),
            media_type=VERIFICATION_RECEIPT_MEDIA_TYPE,
        ),
    )


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
        repository = create_git_repo(root / "repository")
        bundle_store = root / "bundles"
        bundle = FilesystemWorkspaceBundleStore(bundle_store).create(repository)
        self.transport = DockerVerifierTransport(
            bundle_store=bundle_store,
            artifact_root=root / "artifacts",
            timeout_seconds=0.1,
        )
        self.request = _request(bundle=bundle)
        self.observed_bundle_names: tuple[str, ...] = ()
        self.observed_bundle_source: Path | None = None

    def execute_with(self, completed: SimpleNamespace) -> VerificationServiceResult:
        def run(
            _: DockerVerifierTransport,
            command: list[str],
        ) -> SimpleNamespace:
            if completed.returncode in {0, 1}:
                bundle_mount = next(
                    item
                    for item in command
                    if item.endswith(",dst=/bundles,readonly")
                )
                bundle_source = Path(
                    bundle_mount.split(",src=", 1)[1].split(",dst=", 1)[0]
                )
                self.observed_bundle_source = bundle_source
                self.observed_bundle_names = tuple(
                    sorted(path.name for path in bundle_source.iterdir())
                )
                try:
                    raw = json.loads(
                        [
                            line
                            for line in completed.stdout.splitlines()
                            if line.strip()
                        ][-1]
                    )
                    result = VerificationServiceResult.from_dict(raw)
                except (IndexError, ValueError):
                    return completed
                mount = next(
                    item
                    for item in command
                    if item.endswith(",dst=/artifacts")
                )
                staging_root = Path(mount.split(",src=", 1)[1].split(",dst=", 1)[0])
                receipt_path = staging_root / result.receipt_artifact.artifact_id
                receipt_path.parent.mkdir(parents=True)
                receipt_path.write_text(
                    json.dumps(result.receipt.to_dict(), indent=2, sort_keys=True)
                    + "\n",
                    encoding="utf-8",
                )
            return completed

        with patch.object(
            DockerVerifierTransport,
            "_run_container",
            autospec=True,
            side_effect=run,
        ):
            return self.transport.execute(self.request)

    def test_bundle_mount_is_a_fresh_exact_request_only_view(self) -> None:
        (self.transport.bundle_store / ("f" * 64 + ".tar")).write_bytes(b"decoy")
        (self.transport.bundle_store / ("f" * 64 + ".json")).write_text(
            "{}",
            encoding="utf-8",
        )
        expected = _result(self.request)

        self.execute_with(_completed(stdout=json.dumps(expected.to_dict())))

        digest = self.request.workspace_bundle.archive_sha256.removeprefix("sha256:")
        self.assertEqual(
            self.observed_bundle_names,
            (f"{digest}.json", f"{digest}.tar"),
        )
        self.assertIsNotNone(self.observed_bundle_source)
        self.assertNotEqual(
            self.observed_bundle_source,
            self.transport.bundle_store,
        )

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
            __: Path,
            *,
            staging_root: Path,
            bundle_view: Path,
            cidfile: Path,
        ) -> list[str]:
            del staging_root, bundle_view
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
            with self.assertRaisesRegex(
                DockerVerifierError,
                "output exceeded limit",
            ):
                transport.execute(self.request)

        self.assertLess(time.monotonic() - started, 3)
        self.assertIn(("docker", "rm", "--force", "a" * 64), cleanup_calls)
        self.assertFalse((transport.artifact_root / self.request.run_id).exists())

    def test_os_and_timeout_failures_are_closed(self) -> None:
        failures = (
            OSError("docker unavailable"),
            subprocess.TimeoutExpired(("docker", "run"), 0.1),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch.object(
                    DockerVerifierTransport,
                    "_run_container",
                    autospec=True,
                    side_effect=failure,
                ):
                    with self.assertRaisesRegex(
                        DockerVerifierError,
                        "container execution failed",
                    ) as raised:
                        self.transport.execute(self.request)
                self.assertIs(raised.exception.__cause__, failure)

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
            with self.assertRaises(DockerVerifierError):
                self.transport.execute(self.request)

        self.assertIn(("docker", "rm", "--force", "a" * 64), calls)

    def test_receipt_is_host_validated_before_atomic_publication(self) -> None:
        expected = _result(self.request)
        completed = _completed(stdout=json.dumps(expected.to_dict()))

        def omit_receipt(
            _: DockerVerifierTransport,
            __: list[str],
        ) -> SimpleNamespace:
            return completed

        with patch.object(
            DockerVerifierTransport,
            "_run_container",
            autospec=True,
            side_effect=omit_receipt,
        ):
            with self.assertRaisesRegex(
                DockerVerifierError,
                "artifact failed host validation",
            ):
                self.transport.execute(self.request)

        self.assertFalse((self.transport.artifact_root / self.request.run_id).exists())

    def test_non_protocol_exit_codes_include_bounded_stderr_or_fallback(self) -> None:
        cases = (
            ("prefix-" + "x" * 2100, "x" * 2000),
            ("   ", "verifier container failed"),
        )
        for stderr, expected in cases:
            with self.subTest(has_detail=bool(stderr.strip())):
                with self.assertRaisesRegex(DockerVerifierError, expected):
                    self.execute_with(
                        _completed(stdout="ignored", returncode=125, stderr=stderr)
                    )

    def test_empty_and_invalid_output_are_rejected(self) -> None:
        cases = (
            ("\n  \n", "returned no result"),
            ("service log\nnot-json\n", "invalid JSON"),
            ('{"value": 1, "value": 2}\n', "duplicate field"),
        )
        for stdout, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(DockerVerifierError, expected):
                    self.execute_with(_completed(stdout=stdout))

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
                "different workspace bundle",
            ),
            (
                _result(self.request, profile_digest=_digest("7")),
                "different verification profile",
            ),
            (other_run_result, "different run"),
        )
        for result, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(DockerVerifierError, expected):
                    self.execute_with(
                        _completed(stdout=json.dumps(result.to_dict()))
                    )

    def test_last_nonempty_line_is_a_valid_result_and_exit_one_is_protocol_data(self) -> None:
        expected = _result(self.request, passed=False)

        result = self.execute_with(
            _completed(
                stdout="diagnostic line\n\n" + json.dumps(expected.to_dict()) + "\n",
                returncode=1,
                stderr="verification failed",
            )
        )

        self.assertEqual(result, expected)
        self.assertEqual(self.transport.read_receipt(result.receipt_artifact), expected.receipt)
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
