from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sisyphus_harness.authority import (
    AuthorityError,
    authority_database_path,
    authority_root,
    git_common_dir,
    knowledge_index_path,
)

from .helpers import create_git_repo, run_git


class AuthorityTests(unittest.TestCase):
    def test_authority_uses_git_common_directory_for_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = create_git_repo(root / "repository")
            worktree = root / "worktree"
            run_git(repository, "worktree", "add", "-q", "-b", "test-worktree", str(worktree))

            self.assertEqual(git_common_dir(worktree), (repository / ".git").resolve())
            self.assertEqual(
                authority_root(worktree),
                (repository / ".git" / "sisyphus-harness").resolve(),
            )
            self.assertEqual(
                authority_database_path(worktree).parent,
                authority_root(worktree),
            )
            self.assertEqual(
                knowledge_index_path(worktree),
                authority_root(worktree) / "knowledge-index.sqlite3",
            )

    def test_non_git_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(AuthorityError):
                git_common_dir(Path(directory))

    def test_existing_authority_symlink_cannot_escape_common_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = create_git_repo(root / "repository")
            outside = root / "outside"
            outside.mkdir()
            (repository / ".git" / "sisyphus-harness").symlink_to(
                outside,
                target_is_directory=True,
            )

            with self.assertRaises(AuthorityError):
                authority_root(repository)


if __name__ == "__main__":
    unittest.main()
