from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sisyphus_harness.contracts.verifier_assets import VerifierAssetBundleRef
from sisyphus_harness.infra import verifier_assets as verifier_assets_module
from sisyphus_harness.infra.verifier_assets import (
    FilesystemVerifierAssetBundleStore,
    VerifierAssetError,
    verifier_asset_tree_hash,
)
from sisyphus_harness.infra.workspace_bundle import FilesystemWorkspaceBundleStore


class VerifierAssetBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.source = self.root / "source"
        (self.source / "fixtures").mkdir(parents=True)
        check = self.source / "check.py"
        check.write_text("print('verified')\n", encoding="utf-8")
        check.chmod(0o755)
        (self.source / "fixtures" / "input.json").write_text(
            '{"value":1}\n',
            encoding="utf-8",
        )
        self.store = FilesystemVerifierAssetBundleStore(self.root / "store")

    def test_bundle_stores_share_the_default_manifest_limit(self) -> None:
        workspace_store = FilesystemWorkspaceBundleStore(
            self.root / "workspace-store"
        )
        self.assertEqual(
            self.store.max_manifest_bytes,
            workspace_store.max_manifest_bytes,
        )

    def test_bundle_is_deterministic_strict_and_materializes_exact_tree(self) -> None:
        first = self.store.create(self.source)
        second = self.store.create(self.source)

        self.assertEqual(first, second)
        self.assertEqual(self.store.load(first.bundle_id), first)
        self.assertEqual(first.entry_count, 2)
        self.assertEqual(
            first.bundle_id,
            f"verifier-assets:{first.manifest_sha256}",
        )

        destination = self.root / "materialized"
        tree_hash = self.store.materialize(first, destination)

        self.assertEqual(tree_hash, first.tree_hash)
        self.assertEqual(verifier_asset_tree_hash(destination), first.tree_hash)
        self.assertEqual(
            (destination / "fixtures" / "input.json").read_text(encoding="utf-8"),
            '{"value":1}\n',
        )
        self.assertTrue((destination / "check.py").stat().st_mode & 0o111)
        self.assertEqual(destination.stat().st_mode & 0o222, 0)
        self.assertEqual((destination / "check.py").stat().st_mode & 0o222, 0)

    def test_reference_rejects_unknown_tampered_and_invalid_identity(self) -> None:
        reference = self.store.create(self.source)
        unknown = reference.to_dict()
        unknown["unexpected"] = True
        tampered = reference.to_dict()
        tampered["entry_count"] = 3

        for payload, expected in (
            (unknown, "unknown fields"),
            (tampered, "digest does not match content"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, expected):
                    VerifierAssetBundleRef.from_dict(payload)

        with self.assertRaisesRegex(ValueError, "differ"):
            VerifierAssetBundleRef(
                bundle_id="verifier-assets:sha256:" + "a" * 64,
                manifest_sha256="sha256:" + "b" * 64,
                tree_hash="sha256:" + "c" * 64,
                total_size_bytes=1,
                entry_count=1,
            )

    def test_reference_rejects_invalid_direct_and_wire_values(self) -> None:
        reference = self.store.create(self.source)
        constructors = (
            (dict(bundle_id="invalid"), "bundle ID is invalid"),
            (dict(manifest_sha256="invalid"), "manifest digest must be SHA-256"),
            (dict(tree_hash="invalid"), "tree hash must be SHA-256"),
            (dict(total_size_bytes=True), "non-negative integer"),
            (dict(entry_count=0), "at least one file"),
            (dict(schema_version="future"), "unsupported"),
        )
        for changes, expected in constructors:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, expected):
                    replace(reference, **changes)

        payloads = (
            (object(), "must be an object"),
            (dict(reference.to_dict(), total_size_bytes=-1), "non-negative integer"),
            (dict(reference.to_dict(), reference_digest="bad"), "must be SHA-256"),
            (dict(reference.to_dict(), bundle_id=""), "non-empty string"),
        )
        for payload, expected in payloads:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, expected):
                    VerifierAssetBundleRef.from_dict(payload)

    def test_store_limits_empty_source_and_invalid_materialization_fail_closed(
        self,
    ) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaisesRegex(VerifierAssetError, "at least one file"):
            self.store.create(empty)

        cases = (
            ({"max_entries": 1}, "entry limit"),
            ({"max_file_bytes": 1}, "file exceeds size limit"),
            ({"max_bundle_bytes": 1}, "bundle exceeds size limit"),
            ({"max_manifest_bytes": 1}, "manifest exceeds size limit"),
        )
        for index, (limits, expected) in enumerate(cases):
            with self.subTest(limits=limits):
                store = FilesystemVerifierAssetBundleStore(
                    self.root / f"limited-{index}",
                    **limits,
                )
                with self.assertRaisesRegex(VerifierAssetError, expected):
                    store.create(self.source)

        for invalid in (0, -1):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "must be positive"):
                    FilesystemVerifierAssetBundleStore(
                        self.root / "invalid-limit",
                        max_entries=invalid,
                    )

        reference = self.store.create(self.source)
        with self.assertRaisesRegex(TypeError, "exact bundle reference"):
            self.store.materialize(object(), self.root / "invalid")  # type: ignore[arg-type]
        with self.assertRaisesRegex(VerifierAssetError, "entry limit"):
            FilesystemVerifierAssetBundleStore(
                self.store.root,
                max_entries=1,
            ).materialize(reference, self.root / "too-many")
        with self.assertRaisesRegex(VerifierAssetError, "size limit"):
            FilesystemVerifierAssetBundleStore(
                self.store.root,
                max_bundle_bytes=1,
            ).materialize(reference, self.root / "too-large")

    def test_reference_mismatch_and_existing_collision_are_rejected(self) -> None:
        reference = self.store.create(self.source)
        digest = reference.bundle_id.rsplit(":", 1)[-1]
        reference_path = self.store.root / f"{digest}.json"
        mismatched = replace(reference, tree_hash="sha256:" + "f" * 64)
        reference_path.write_text(
            json.dumps(mismatched.to_dict()),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(VerifierAssetError, "digest collision"):
            self.store.create(self.source)

        other_source = self.root / "other-source"
        other_source.mkdir()
        (other_source / "different.txt").write_text("different", encoding="utf-8")
        other = self.store.create(other_source)
        reference_path.write_text(json.dumps(other.to_dict()), encoding="utf-8")
        with self.assertRaisesRegex(VerifierAssetError, "ID mismatch"):
            self.store.load(reference.bundle_id)

    def test_create_recovers_a_fsynced_bundle_missing_only_its_reference(self) -> None:
        reference = self.store.create(self.source)
        digest = reference.bundle_id.rsplit(":", 1)[-1]
        (self.store.root / f"{digest}.json").unlink()

        recovered = self.store.create(self.source)

        self.assertEqual(recovered, reference)
        self.assertEqual(self.store.load(reference.bundle_id), reference)

    def test_invalid_roots_and_bundle_ids_fail_closed(self) -> None:
        regular_file = self.root / "regular-file"
        regular_file.write_text("not a directory", encoding="utf-8")
        for source in (regular_file, self.root / "missing"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(VerifierAssetError, "not a regular directory|not a directory"):
                    self.store.create(source)
        with self.assertRaisesRegex(VerifierAssetError, "invalid.*bundle ID"):
            self.store.load("verifier-assets:invalid")
        with self.assertRaisesRegex(VerifierAssetError, "not a regular directory"):
            verifier_asset_tree_hash(regular_file)

    def test_materialization_enforces_file_limit_and_final_tree_digest(self) -> None:
        reference = self.store.create(self.source)
        with self.assertRaisesRegex(VerifierAssetError, "file exceeds limit"):
            FilesystemVerifierAssetBundleStore(
                self.store.root,
                max_file_bytes=1,
            ).materialize(reference, self.root / "file-too-large")

        with patch.object(
            verifier_assets_module,
            "verifier_asset_tree_hash",
            return_value="sha256:" + "f" * 64,
        ):
            with self.assertRaisesRegex(VerifierAssetError, "tree mismatch"):
                self.store.materialize(reference, self.root / "wrong-tree")

    def test_manifest_reader_rejects_digest_json_and_shape_corruption(self) -> None:
        reference = self.store.create(self.source)
        digest = reference.bundle_id.rsplit(":", 1)[-1]
        bundle_path = self.store.root / digest
        manifest_path = bundle_path / "manifest.json"
        manifest_path.chmod(0o644)
        manifest_path.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(VerifierAssetError, "manifest digest mismatch"):
            self.store.materialize(reference, self.root / "bad-manifest-digest")

        descriptor = verifier_assets_module._open_directory(bundle_path)
        try:
            for content, expected in ((b"not-json", "invalid"), (b"[]", "object")):
                with self.subTest(expected=expected):
                    manifest_path.write_bytes(content)
                    content_digest = "sha256:" + hashlib.sha256(content).hexdigest()
                    matching = replace(
                        reference,
                        bundle_id=f"verifier-assets:{content_digest}",
                        manifest_sha256=content_digest,
                    )
                    with self.assertRaisesRegex(VerifierAssetError, expected):
                        self.store._read_manifest(descriptor, matching)
        finally:
            os.close(descriptor)

    def test_stable_identity_changes_are_rejected_at_open_boundaries(self) -> None:
        with patch.object(
            verifier_assets_module,
            "_same_stable_file",
            return_value=False,
        ):
            with self.assertRaisesRegex(VerifierAssetError, "changed while being opened"):
                self.store.create(self.source)

        reference = self.store.create(self.source)
        with patch.object(
            verifier_assets_module,
            "_same_stable_file",
            side_effect=(True, True, False),
        ):
            with self.assertRaisesRegex(VerifierAssetError, "changed while being opened"):
                self.store.materialize(reference, self.root / "changed-bundle")

    def test_manifest_validator_rejects_every_untrusted_shape(self) -> None:
        reference = self.store.create(self.source)
        digest = reference.bundle_id.rsplit(":", 1)[-1]
        manifest = json.loads(
            (self.store.root / digest / "manifest.json").read_text(encoding="utf-8")
        )

        cases: list[tuple[dict[str, object], str]] = []

        invalid = copy.deepcopy(manifest)
        invalid["unexpected"] = True
        cases.append((invalid, "fields are invalid"))

        invalid = copy.deepcopy(manifest)
        invalid["schema_version"] = "future"
        cases.append((invalid, "unsupported"))

        invalid = copy.deepcopy(manifest)
        invalid["tree_hash"] = "sha256:" + "f" * 64
        cases.append((invalid, "tree hash mismatch"))

        invalid = copy.deepcopy(manifest)
        invalid["total_size_bytes"] = reference.total_size_bytes + 1
        cases.append((invalid, "size mismatch"))

        invalid = copy.deepcopy(manifest)
        invalid["entries"] = "not-a-list"
        cases.append((invalid, "entry count mismatch"))

        invalid = copy.deepcopy(manifest)
        entries = invalid["entries"]
        assert isinstance(entries, list)
        entries[0]["unexpected"] = True
        cases.append((invalid, "entry is invalid"))

        invalid = copy.deepcopy(manifest)
        entries = invalid["entries"]
        assert isinstance(entries, list)
        entries[1]["path"] = entries[0]["path"]
        cases.append((invalid, "unique and sorted"))

        invalid = copy.deepcopy(manifest)
        entries = invalid["entries"]
        assert isinstance(entries, list)
        entries[0]["sha256"] = "bad"
        cases.append((invalid, "file digest is invalid"))

        invalid = copy.deepcopy(manifest)
        entries = invalid["entries"]
        assert isinstance(entries, list)
        entries[0]["size_bytes"] = True
        cases.append((invalid, "size_bytes is invalid"))

        invalid = copy.deepcopy(manifest)
        entries = invalid["entries"]
        assert isinstance(entries, list)
        entries[0]["executable"] = "yes"
        cases.append((invalid, "executable flag is invalid"))

        invalid = copy.deepcopy(manifest)
        entries = invalid["entries"]
        assert isinstance(entries, list)
        entries[0]["sha256"] = "sha256:" + "f" * 64
        cases.append((invalid, "entry hash mismatch"))

        for candidate, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(VerifierAssetError, expected):
                    verifier_assets_module._manifest_entries(candidate, reference)

        for value in (None, "", "../escape", "/absolute", "a\\b", "a//b"):
            with self.subTest(path=value):
                with self.assertRaisesRegex(VerifierAssetError, "path is (invalid|unsafe)"):
                    verifier_assets_module._manifest_path({"path": value})

    def test_source_and_materialized_trees_reject_symlinks(self) -> None:
        outside = self.root / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        (self.source / "escape").symlink_to(outside)

        with self.assertRaisesRegex(VerifierAssetError, "symlinks are forbidden"):
            self.store.create(self.source)

        (self.source / "escape").unlink()
        reference = self.store.create(self.source)
        materialized = self.root / "materialized"
        self.store.materialize(reference, materialized)
        materialized.chmod(0o755)
        (materialized / "check.py").chmod(0o644)
        (materialized / "check.py").unlink()
        (materialized / "check.py").symlink_to(outside)
        with self.assertRaisesRegex(VerifierAssetError, "contains a symlink"):
            verifier_asset_tree_hash(materialized)

    def test_symlinked_source_store_reference_and_destination_fail_closed(self) -> None:
        source_link = self.root / "source-link"
        source_link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(VerifierAssetError, "regular directory"):
            self.store.create(source_link)

        reference = self.store.create(self.source)
        digest = reference.bundle_id.rsplit(":", 1)[-1]
        reference_path = self.store.root / f"{digest}.json"
        reference_path.unlink()
        reference_path.symlink_to(self.root / "missing.json")
        with self.assertRaisesRegex(VerifierAssetError, "reference is invalid"):
            self.store.load(reference.bundle_id)

        reference_path.unlink()
        reference_path.write_text(json.dumps(reference.to_dict()), encoding="utf-8")
        destination_target = self.root / "target"
        destination_target.mkdir()
        destination = self.root / "destination"
        destination.symlink_to(destination_target, target_is_directory=True)
        with self.assertRaisesRegex(VerifierAssetError, "already exists"):
            self.store.materialize(reference, destination)

    def test_stored_file_mutation_is_detected_before_public_mount(self) -> None:
        reference = self.store.create(self.source)
        digest = reference.bundle_id.rsplit(":", 1)[-1]
        stored = self.store.root / digest / "files" / "check.py"
        stored.chmod(0o644)
        stored.write_text("print('tampered')\n", encoding="utf-8")

        with self.assertRaisesRegex(VerifierAssetError, "digest mismatch"):
            self.store.materialize(reference, self.root / "materialized")

    def test_source_mutation_after_copy_is_rejected(self) -> None:
        original = verifier_assets_module.verifier_asset_tree_hash
        calls = 0

        def mutate_before_rescan(root: Path) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                (self.source / "check.py").write_text(
                    "print('changed')\n",
                    encoding="utf-8",
                )
            return original(root)

        with patch.object(
            verifier_assets_module,
            "verifier_asset_tree_hash",
            side_effect=mutate_before_rescan,
        ):
            with self.assertRaisesRegex(VerifierAssetError, "changed while being copied"):
                self.store.create(self.source)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO requires POSIX")
    def test_special_source_entry_is_rejected(self) -> None:
        fifo = self.source / "pipe"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(VerifierAssetError, "entry type is forbidden"):
            self.store.create(self.source)

        fifo.unlink()
        reference = self.store.create(self.source)
        materialized = self.root / "materialized-special"
        self.store.materialize(reference, materialized)
        materialized.chmod(0o755)
        os.mkfifo(materialized / "pipe")
        with self.assertRaisesRegex(VerifierAssetError, "forbidden entry"):
            verifier_asset_tree_hash(materialized)


if __name__ == "__main__":
    unittest.main()
