from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sisyphus_harness.workspace import (
    PathBoundaryError,
    contained_path,
    snapshot_workspace,
)

from .helpers import create_git_repo, run_git


class WorkspaceTests(unittest.TestCase):
    def test_contained_path_rejects_parent_and_absolute_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            outside = Path(directory) / "outside.txt"

            with self.assertRaises(PathBoundaryError):
                contained_path(root, "../outside.txt", require_relative=True)
            with self.assertRaises(PathBoundaryError):
                contained_path(root, outside, require_relative=True)
            with self.assertRaises(PathBoundaryError):
                contained_path(root, ".", require_relative=True)

    def test_snapshot_changes_for_unstaged_staged_and_untracked_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = create_git_repo(Path(directory) / "repository")
            baseline = snapshot_workspace(repository)

            (repository / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
            unstaged = snapshot_workspace(repository)
            run_git(repository, "add", "tracked.txt")
            staged = snapshot_workspace(repository)
            (repository / "untracked.txt").write_text("one\n", encoding="utf-8")
            untracked = snapshot_workspace(repository)
            (repository / "untracked.txt").write_text("two\n", encoding="utf-8")
            changed_untracked = snapshot_workspace(repository)

            hashes = {
                baseline.state_hash,
                unstaged.state_hash,
                staged.state_hash,
                untracked.state_hash,
                changed_untracked.state_hash,
            }
            self.assertEqual(len(hashes), 5)
            self.assertEqual(unstaged.changed_paths, ("tracked.txt",))
            self.assertEqual(
                untracked.changed_paths,
                ("tracked.txt", "untracked.txt"),
            )

    def test_untracked_symlink_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = create_git_repo(root / "repository")
            outside = root / "outside.txt"
            outside.write_text("secret\n", encoding="utf-8")
            (repository / "escape").symlink_to(outside)

            with self.assertRaises(PathBoundaryError):
                snapshot_workspace(repository)

    def test_untracked_internal_symlink_is_hashed_as_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = create_git_repo(Path(directory) / "repository")
            target = repository / "target.txt"
            target.write_text("one\n", encoding="utf-8")
            link = repository / "link"
            link.symlink_to("target.txt")
            first = snapshot_workspace(repository)
            link.unlink()
            link.symlink_to("tracked.txt")
            second = snapshot_workspace(repository)

            self.assertNotEqual(first.state_hash, second.state_hash)


if __name__ == "__main__":
    unittest.main()
