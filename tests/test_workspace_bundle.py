from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from sisyphus_harness.adapters.workspace_state import TreeHashWorkspaceStateAdapter
from sisyphus_harness.contracts import CommandSpec, WorkspaceBundleRef
from sisyphus_harness.infra.workspace_bundle import (
    FilesystemWorkspaceBundleStore,
    WorkspaceBundleError,
    _add_file,
    workspace_tree_hash,
)
from sisyphus_harness.workspace import snapshot_workspace
from sisyphus_harness.verifier import BoundedVerifier

from .helpers import create_git_repo, run_git


class WorkspaceBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.repository = create_git_repo(self.root / "repository")
        self.store_root = self.root / "bundle-store"
        self.store = FilesystemWorkspaceBundleStore(self.store_root)

    def test_bundle_is_deterministic_and_materializes_exact_tree(self) -> None:
        script = self.repository / "script.sh"
        script.write_text("#!/bin/sh\nprintf 'ok\\n'\n", encoding="utf-8")
        script.chmod(0o755)
        (self.repository / "nested").mkdir()
        (self.repository / "nested" / "data.txt").write_text(
            "payload\n", encoding="utf-8"
        )
        (self.repository / "script-link").symlink_to("script.sh")
        (self.repository / ".gitignore").write_text("ignored.bin\n", encoding="utf-8")
        (self.repository / "ignored.bin").write_bytes(b"not bundled")
        run_git(self.repository, "add", ".gitignore")
        run_git(self.repository, "commit", "-q", "-m", "add ignore rules")
        source = snapshot_workspace(self.repository)

        first = self.store.create(self.repository)
        second = self.store.create(self.repository)
        loaded = self.store.load(first.bundle_id)
        destination = self.root / "materialized"
        materialized_tree_hash = self.store.materialize(first, destination)

        self.assertEqual(first, second)
        self.assertEqual(loaded, first)
        self.assertEqual(first.source_commit_sha, source.commit_sha)
        self.assertEqual(first.source_state_hash, source.state_hash)
        self.assertEqual(first.changed_paths, source.changed_paths)
        self.assertEqual(materialized_tree_hash, first.tree_hash)
        self.assertEqual((destination / "tracked.txt").read_text(), "baseline\n")
        self.assertEqual((destination / "nested" / "data.txt").read_text(), "payload\n")
        self.assertTrue((destination / "script-link").is_symlink())
        self.assertEqual(os.readlink(destination / "script-link"), "script.sh")
        self.assertTrue((destination / "script.sh").stat().st_mode & stat.S_IXUSR)
        self.assertFalse((destination / "ignored.bin").exists())
        self.assertFalse((destination / ".git").exists())

        (destination / "tracked.txt").write_text("mutated\n", encoding="utf-8")
        changed_tree_hash = workspace_tree_hash(destination)
        self.assertNotEqual(changed_tree_hash, first.tree_hash)

    def test_staged_and_unstaged_states_produce_distinct_bundles(self) -> None:
        tracked = self.repository / "tracked.txt"
        tracked.write_text("changed\n", encoding="utf-8")
        run_git(self.repository, "add", "tracked.txt")
        staged = self.store.create(self.repository)

        run_git(self.repository, "reset", "HEAD", "tracked.txt")
        unstaged = self.store.create(self.repository)

        self.assertNotEqual(staged.source_state_hash, unstaged.source_state_hash)
        self.assertNotEqual(staged.bundle_id, unstaged.bundle_id)
        self.assertEqual(staged.tree_hash, unstaged.tree_hash)

    def test_materialized_tree_adapter_supports_verification_without_git(self) -> None:
        ref = self.store.create(self.repository)
        destination = self.root / "verifier-workspace"
        self.store.materialize(ref, destination)
        adapter = TreeHashWorkspaceStateAdapter(ref.source_commit_sha)
        verifier = BoundedVerifier(
            self.root / "tree-verification",
            workspace_state=adapter,
        )

        receipt = verifier.verify(
            destination,
            (
                CommandSpec(
                    name="read-only",
                    argv=(sys.executable, "-c", "from pathlib import Path; Path('tracked.txt').read_text()"),
                    timeout_seconds=5,
                    criteria=("workspace is readable",),
                ),
            ),
            run_id="tree-read-only",
        )

        self.assertTrue(receipt.passed)
        self.assertEqual(receipt.workspace_state_before, ref.tree_hash)
        self.assertEqual(receipt.workspace_state_after, ref.tree_hash)
        self.assertEqual(receipt.worktree_commit_sha, ref.source_commit_sha)

    def test_materialized_tree_adapter_detects_verifier_mutation(self) -> None:
        ref = self.store.create(self.repository)
        destination = self.root / "mutating-verifier-workspace"
        self.store.materialize(ref, destination)
        verifier = BoundedVerifier(
            self.root / "mutating-tree-verification",
            workspace_state=TreeHashWorkspaceStateAdapter(ref.source_commit_sha),
        )

        receipt = verifier.verify(
            destination,
            (
                CommandSpec(
                    name="mutating",
                    argv=(
                        sys.executable,
                        "-c",
                        "from pathlib import Path; Path('tracked.txt').write_text('changed\\n')",
                    ),
                    timeout_seconds=5,
                    criteria=("workspace remains immutable",),
                ),
            ),
            run_id="tree-mutation",
        )

        self.assertFalse(receipt.passed)
        self.assertFalse(receipt.workspace_unchanged)
        self.assertEqual(receipt.commands[0].failure_category, "workspace_mutation")

    def test_reference_parser_is_strict_and_normalizes_changed_paths(self) -> None:
        ref = self.store.create(self.repository)
        payload = ref.to_dict()
        payload["changed_paths"] = ["z.py", "a.py"]
        parsed = WorkspaceBundleRef.from_dict(payload)
        self.assertEqual(parsed.changed_paths, ("a.py", "z.py"))

        with self.assertRaisesRegex(ValueError, "unknown fields"):
            WorkspaceBundleRef.from_dict({**payload, "path": "/tmp/archive"})
        with self.assertRaisesRegex(ValueError, "unsafe"):
            WorkspaceBundleRef.from_dict({**payload, "changed_paths": ["../escape"]})
        with self.assertRaisesRegex(ValueError, "must match"):
            WorkspaceBundleRef.from_dict(
                {**payload, "bundle_id": "workspace:sha256:" + "0" * 64}
            )

    def test_archive_tampering_is_detected_before_extraction(self) -> None:
        ref = self.store.create(self.repository)
        digest = ref.archive_sha256.removeprefix("sha256:")
        archive_path = self.store_root / f"{digest}.tar"
        with archive_path.open("r+b") as archive:
            original = archive.read(1)
            archive.seek(0)
            archive.write(bytes([original[0] ^ 0x01]))

        with self.assertRaisesRegex(WorkspaceBundleError, "digest mismatch"):
            self.store.materialize(ref, self.root / "tampered")

    def test_materializer_rejects_unsafe_tar_entry_types_and_paths(self) -> None:
        cases = (
            ("parent", _regular_member("../outside.txt", b"escape"), "unsafe"),
            ("absolute", _regular_member("/outside.txt", b"escape"), "unsafe"),
            ("device", _special_member("device", tarfile.CHRTYPE), "forbidden"),
            ("hardlink", _hardlink_member("hard", "target"), "forbidden"),
            ("symlink", _symlink_member("link", "../outside"), "escapes"),
        )
        for label, member, error in cases:
            with self.subTest(label=label):
                ref = self._write_untrusted_archive(label, member)
                with self.assertRaisesRegex(WorkspaceBundleError, error):
                    self.store.materialize(ref, self.root / f"extract-{label}")
        self.assertFalse((self.root / "outside.txt").exists())

    def test_store_enforces_entry_and_file_size_limits(self) -> None:
        (self.repository / "extra.txt").write_text("extra\n", encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceBundleError, "entry limit"):
            FilesystemWorkspaceBundleStore(
                self.root / "small-entry-store",
                max_entries=1,
            ).create(self.repository)

        with self.assertRaisesRegex(WorkspaceBundleError, "manifest exceeds"):
            FilesystemWorkspaceBundleStore(
                self.root / "small-manifest-store",
                max_manifest_bytes=100,
            ).create(self.repository)

    def test_large_manifest_round_trips_at_default_limits(self) -> None:
        files = self.repository / "many"
        files.mkdir()
        for index in range(7000):
            (files / f"{index:05d}.txt").touch()
        run_git(self.repository, "add", "many")
        run_git(self.repository, "commit", "-q", "-m", "add many files")

        ref = self.store.create(self.repository)
        destination = self.root / "large-materialized"
        materialized = self.store.materialize(ref, destination)

        self.assertEqual(ref.entry_count, 7001)
        self.assertEqual(materialized, ref.tree_hash)
        self.assertEqual(len(list((destination / "many").iterdir())), 7000)

    def test_source_file_replacement_between_stat_and_open_is_rejected(self) -> None:
        source = self.repository / "tracked.txt"
        original = source.lstat()
        source.unlink()
        source.write_text("replacement\n", encoding="utf-8")

        with tarfile.open(self.root / "race.tar", mode="w") as archive:
            with self.assertRaisesRegex(WorkspaceBundleError, "changed while bundling"):
                _add_file(
                    archive,
                    source,
                    "tracked.txt",
                    original,
                    max_file_bytes=1024,
                )

    def test_archive_and_store_directory_are_synced_before_reference(self) -> None:
        with (
            patch(
                "sisyphus_harness.infra.workspace_bundle._fsync_file",
                wraps=__import__(
                    "sisyphus_harness.infra.workspace_bundle",
                    fromlist=["_fsync_file"],
                )._fsync_file,
            ) as fsync_file,
            patch(
                "sisyphus_harness.infra.workspace_bundle._fsync_directory",
                wraps=__import__(
                    "sisyphus_harness.infra.workspace_bundle",
                    fromlist=["_fsync_directory"],
                )._fsync_directory,
            ) as fsync_directory,
        ):
            ref = self.store.create(self.repository)

        fsync_file.assert_called_once()
        fsync_directory.assert_called_once_with(self.store_root)
        self.assertTrue(
            (self.store_root / f"{ref.archive_sha256.removeprefix('sha256:')}.json").is_file()
        )
        with self.assertRaisesRegex(WorkspaceBundleError, "file exceeds"):
            FilesystemWorkspaceBundleStore(
                self.root / "small-file-store",
                max_file_bytes=2,
            ).create(self.repository)

    def test_materializer_enforces_reference_limits_and_rejects_symlinks(self) -> None:
        ref = self.store.create(self.repository)
        archive_path = self.store_root / f"{ref.archive_sha256[7:]}.tar"

        limited = FilesystemWorkspaceBundleStore(
            self.store_root,
            max_bundle_bytes=ref.size_bytes - 1,
        )
        with self.assertRaisesRegex(WorkspaceBundleError, "archive size limit"):
            limited.materialize(ref, self.root / "oversized")

        with self.assertRaisesRegex(WorkspaceBundleError, "entry limit"):
            FilesystemWorkspaceBundleStore(
                self.store_root,
                max_entries=1,
            ).materialize(
                replace(ref, entry_count=2),
                self.root / "too-many-entries",
            )

        backing_archive = self.store_root / "backing.tar"
        archive_path.replace(backing_archive)
        archive_path.symlink_to(backing_archive)
        with self.assertRaisesRegex(WorkspaceBundleError, "archive not found"):
            self.store.materialize(ref, self.root / "archive-symlink")
        archive_path.unlink()
        backing_archive.replace(archive_path)

        destination = self.root / "destination-symlink"
        destination.symlink_to(self.root / "elsewhere")
        with self.assertRaisesRegex(WorkspaceBundleError, "destination is a symlink"):
            self.store.materialize(ref, destination)

    def _write_untrusted_archive(
        self,
        label: str,
        member: tuple[tarfile.TarInfo, bytes | None],
    ) -> WorkspaceBundleRef:
        self.store_root.mkdir(parents=True, exist_ok=True)
        temporary = self.store_root / f"{label}.tmp"
        info, content = member
        with tarfile.open(temporary, mode="w") as archive:
            archive.addfile(info, io.BytesIO(content) if content is not None else None)
        raw = temporary.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        archive_path = self.store_root / f"{digest}.tar"
        temporary.replace(archive_path)
        source = snapshot_workspace(self.repository)
        return WorkspaceBundleRef(
            bundle_id=f"workspace:sha256:{digest}",
            archive_sha256=f"sha256:{digest}",
            size_bytes=len(raw),
            source_commit_sha=source.commit_sha,
            source_state_hash=source.state_hash,
            tree_hash="sha256:" + "0" * 64,
            changed_paths=source.changed_paths,
            entry_count=1,
        )


def _regular_member(name: str, content: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = 0o644
    return info, content


def _special_member(name: str, kind: bytes) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name)
    info.type = kind
    return info, None


def _hardlink_member(name: str, target: str) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name)
    info.type = tarfile.LNKTYPE
    info.linkname = target
    return info, None


def _symlink_member(name: str, target: str) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    return info, None


if __name__ == "__main__":
    unittest.main()
