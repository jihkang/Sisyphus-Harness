from __future__ import annotations

from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sisyphus_harness.deadline import MonotonicDeadline
from sisyphus_harness.tools import ToolError, WorkspaceTools

from .helpers import create_git_repo


class WorkspaceToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        root = Path(self.temporary_directory.name)
        self.repository = create_git_repo(root / "repository")
        self.tools = WorkspaceTools(
            self.repository,
            max_file_bytes=4096,
            max_output_chars=2000,
        )

    def test_expired_global_deadline_blocks_tool_execution(self) -> None:
        tools = WorkspaceTools(
            self.repository,
            max_file_bytes=4096,
            max_output_chars=2000,
            deadline=MonotonicDeadline(1.0, _clock=lambda: 2.0),
        )

        with self.assertRaisesRegex(ToolError, "deadline exceeded"):
            tools.execute("list_files", {})

    def test_list_read_and_search_are_bounded_read_only_tools(self) -> None:
        (self.repository / "tracked.txt").write_text(
            "first\nneedle\nthird\n",
            encoding="utf-8",
        )
        files = self.tools.execute("list_files", {})
        read = self.tools.execute("read_file", {"path": "tracked.txt"})
        search = self.tools.execute("search_text", {"query": "needle"})

        self.assertFalse(files.mutated)
        self.assertEqual(files.output["files"], ["tracked.txt"])
        self.assertEqual(read.output["content"], "first\nneedle\nthird\n")
        self.assertEqual(read.output["end_line"], 3)
        self.assertEqual(search.output["matches"][0]["line"], 2)
        self.assertEqual(search.output["query_mode"], "literal")

        no_match = self.tools.execute(
            "search_text",
            {"query": r"def parse_port\(value\):"},
        )
        self.assertEqual(no_match.output["matches"], [])
        self.assertEqual(no_match.output["query_mode"], "literal")
        self.assertIn("unescaped literal", no_match.output["hint"])

    def test_root_scope_aliases_are_accepted_for_directory_tools(self) -> None:
        (self.repository / "tracked.txt").write_text(
            "first\nneedle\n",
            encoding="utf-8",
        )

        for root_alias in (".", "./", " . "):
            with self.subTest(root_alias=root_alias):
                listed = self.tools.execute(
                    "list_files",
                    {"prefix": root_alias},
                )
                searched = self.tools.execute(
                    "search_text",
                    {"query": "needle", "path": root_alias},
                )

                self.assertEqual(listed.output["files"], ["tracked.txt"])
                self.assertEqual(searched.output["matches"][0]["path"], "tracked.txt")

    def test_directory_scope_normalization_preserves_path_boundaries(self) -> None:
        for tool, arguments in (
            ("list_files", {"prefix": "/tmp"}),
            ("search_text", {"query": "x", "path": "/tmp"}),
            ("list_files", {"prefix": "child/.."}),
            ("search_text", {"query": "x", "path": "child/.."}),
        ):
            with self.subTest(tool=tool, arguments=arguments):
                with self.assertRaises(ToolError):
                    self.tools.execute(tool, arguments)

    def test_write_requires_hash_for_existing_file_and_preserves_mode(self) -> None:
        path = self.repository / "tracked.txt"
        path.chmod(0o755)
        read = self.tools.execute("read_file", {"path": "tracked.txt"})
        current_hash = read.output["sha256"]

        with self.assertRaisesRegex(ToolError, "require expected_sha256"):
            self.tools.execute(
                "write_file",
                {"path": "tracked.txt", "content": "changed\n"},
            )
        written = self.tools.execute(
            "write_file",
            {
                "path": "tracked.txt",
                "content": "changed\n",
                "expected_sha256": current_hash,
            },
        )

        self.assertTrue(written.mutated)
        self.assertEqual(path.read_text(encoding="utf-8"), "changed\n")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)

    def test_new_file_is_created_with_normal_mode(self) -> None:
        outcome = self.tools.execute(
            "write_file",
            {
                "path": "src/new.py",
                "content": "VALUE = 1\n",
                "expected_sha256": None,
            },
        )
        path = self.repository / "src" / "new.py"

        self.assertTrue(outcome.output["created"])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_writes_reject_ignored_and_lexically_ambiguous_paths(self) -> None:
        (self.repository / ".gitignore").write_text(
            "generated/\n*.secret\n",
            encoding="utf-8",
        )

        for relative in (
            "generated/result.txt",
            "credential.secret",
            "src/../alias.py",
            "./alias.py",
        ):
            with self.subTest(relative=relative):
                with self.assertRaisesRegex(
                    ToolError,
                    "not observable|relative to workspace",
                ):
                    self.tools.execute(
                        "write_file",
                        {
                            "path": relative,
                            "content": "hidden\n",
                            "expected_sha256": None,
                        },
                    )

        self.assertFalse((self.repository / "generated").exists())
        self.assertFalse((self.repository / "credential.secret").exists())

    def test_model_cannot_temporarily_unignore_a_hidden_write(self) -> None:
        root_ignore = self.repository / ".gitignore"
        root_ignore.write_text("generated/\n", encoding="utf-8")
        nested_ignore = self.repository / "nested" / ".gitignore"
        nested_ignore.parent.mkdir()
        nested_ignore.write_text("generated/\n", encoding="utf-8")

        for relative in (".gitignore", "nested/.gitignore"):
            with self.subTest(relative=relative):
                current = self.tools.execute("read_file", {"path": relative})
                for tool, arguments in (
                    (
                        "write_file",
                        {
                            "path": relative,
                            "content": "# temporarily visible\n",
                            "expected_sha256": current.output["sha256"],
                        },
                    ),
                    (
                        "replace_text",
                        {
                            "path": relative,
                            "old": "generated/",
                            "new": "# temporarily visible",
                            "expected_sha256": current.output["sha256"],
                        },
                    ),
                    (
                        "delete_file",
                        {
                            "path": relative,
                            "expected_sha256": current.output["sha256"],
                        },
                    ),
                ):
                    with self.subTest(relative=relative, tool=tool):
                        with self.assertRaisesRegex(
                            ToolError,
                            "ignore controls are protected",
                        ):
                            self.tools.execute(tool, arguments)

        with self.assertRaisesRegex(ToolError, "not observable"):
            self.tools.execute(
                "write_file",
                {
                    "path": "generated/result.txt",
                    "content": "hidden\n",
                    "expected_sha256": None,
                },
            )
        self.assertEqual(root_ignore.read_text(encoding="utf-8"), "generated/\n")
        self.assertFalse((self.repository / "generated").exists())

    def test_replace_and_delete_reject_stale_hash(self) -> None:
        read = self.tools.execute("read_file", {"path": "tracked.txt"})
        current_hash = read.output["sha256"]
        with self.assertRaisesRegex(ToolError, "stale file hash"):
            self.tools.execute(
                "replace_text",
                {
                    "path": "tracked.txt",
                    "old": "baseline",
                    "new": "changed",
                    "expected_sha256": "sha256:" + "0" * 64,
                },
            )
        replaced = self.tools.execute(
            "replace_text",
            {
                "path": "tracked.txt",
                "old": "baseline",
                "new": "changed",
                "expected_sha256": current_hash,
            },
        )
        with self.assertRaisesRegex(ToolError, "stale file hash"):
            self.tools.execute(
                "delete_file",
                {"path": "tracked.txt", "expected_sha256": current_hash},
            )
        deleted = self.tools.execute(
            "delete_file",
            {
                "path": "tracked.txt",
                "expected_sha256": replaced.output["sha256"],
            },
        )

        self.assertTrue(deleted.output["deleted"])
        self.assertFalse((self.repository / "tracked.txt").exists())

    def test_write_boundaries_reject_git_parent_and_symlink_escape(self) -> None:
        outside = Path(self.temporary_directory.name) / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        (self.repository / "escape").symlink_to(outside)

        for path in ("../outside.txt", ".git/config", "escape"):
            with self.subTest(path=path):
                with self.assertRaises(ToolError):
                    self.tools.execute(
                        "write_file",
                        {
                            "path": path,
                            "content": "changed\n",
                            "expected_sha256": None,
                        },
                    )
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")

    def test_read_rejects_symlink_alias_into_git_state(self) -> None:
        alias = self.repository / "git-config-alias"
        alias.symlink_to(".git/config")

        with self.assertRaisesRegex(ToolError, "resolves into protected state"):
            self.tools.execute("read_file", {"path": alias.name})

    def test_search_skips_deleted_tracked_files(self) -> None:
        (self.repository / "kept.txt").write_text("needle\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "kept.txt"],
            cwd=self.repository,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add search fixture"],
            cwd=self.repository,
            capture_output=True,
            check=True,
        )
        (self.repository / "tracked.txt").unlink()

        result = self.tools.execute("search_text", {"query": "needle"})

        self.assertEqual(result.output["matches"][0]["path"], "kept.txt")
        self.assertIn("tracked.txt", result.output["skipped_files"])

    def test_raw_filesystem_failures_are_normalized(self) -> None:
        (self.repository / "directory").mkdir()

        with self.assertRaisesRegex(ToolError, "write_file filesystem operation failed"):
            self.tools.execute(
                "write_file",
                {
                    "path": "directory",
                    "content": "not a directory replacement",
                    "expected_sha256": None,
                },
            )

    def test_replace_requires_exactly_one_old_fragment(self) -> None:
        path = self.repository / "tracked.txt"
        path.write_text("same\nsame\n", encoding="utf-8")
        read = self.tools.execute("read_file", {"path": "tracked.txt"})

        with self.assertRaisesRegex(ToolError, "exactly once"):
            self.tools.execute(
                "replace_text",
                {
                    "path": "tracked.txt",
                    "old": "same",
                    "new": "new",
                    "expected_sha256": read.output["sha256"],
                },
            )

    def test_write_and_replace_reject_no_op_mutations(self) -> None:
        current = self.tools.execute("read_file", {"path": "tracked.txt"})

        with self.assertRaisesRegex(ToolError, "content is unchanged"):
            self.tools.execute(
                "write_file",
                {
                    "path": "tracked.txt",
                    "content": "baseline\n",
                    "expected_sha256": current.output["sha256"],
                },
            )
        with self.assertRaisesRegex(ToolError, "would not change"):
            self.tools.execute(
                "replace_text",
                {
                    "path": "tracked.txt",
                    "old": "baseline",
                    "new": "baseline",
                    "expected_sha256": current.output["sha256"],
                },
            )

        self.assertEqual(
            (self.repository / "tracked.txt").read_text(encoding="utf-8"),
            "baseline\n",
        )

    def test_line_array_modes_preserve_source_backslashes(self) -> None:
        created = self.tools.execute(
            "write_file",
            {
                "path": "pattern.py",
                "content_lines": ["import re", "PATTERN = r'[\\s_]+'", ""],
                "expected_sha256": None,
            },
        )
        replaced = self.tools.execute(
            "replace_text",
            {
                "path": "pattern.py",
                "old_lines": ["import re\nPATTERN = r'[\\s_]+'"],
                "new_lines": ["import re\nPATTERN = r'[-\\s_]+'"],
                "expected_sha256": created.output["sha256"],
            },
        )

        self.assertEqual(created.output["input_mode"], "lines")
        self.assertEqual(replaced.output["input_mode"], "lines")
        self.assertEqual(
            (self.repository / "pattern.py").read_text(encoding="utf-8"),
            "import re\nPATTERN = r'[-\\s_]+'\n",
        )

    def test_line_array_modes_reject_mixed_or_incomplete_arguments(self) -> None:
        current = self.tools.execute("read_file", {"path": "tracked.txt"})
        for arguments in (
            {
                "path": "tracked.txt",
                "old": "baseline",
                "new": "changed",
                "old_lines": ["baseline"],
                "new_lines": ["changed"],
                "expected_sha256": current.output["sha256"],
            },
            {
                "path": "tracked.txt",
                "old_lines": ["baseline"],
                "expected_sha256": current.output["sha256"],
            },
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ToolError):
                    self.tools.execute("replace_text", arguments)

    def test_binary_and_oversized_files_are_not_exposed(self) -> None:
        (self.repository / "binary.bin").write_bytes(b"a\0b")
        (self.repository / "large.txt").write_text("x" * 5000, encoding="utf-8")

        with self.assertRaisesRegex(ToolError, "binary"):
            self.tools.execute("read_file", {"path": "binary.bin"})
        with self.assertRaisesRegex(ToolError, "exceeds"):
            self.tools.execute("read_file", {"path": "large.txt"})

    def test_unknown_tool_arguments_fail_closed(self) -> None:
        with self.assertRaisesRegex(ToolError, "unknown fields"):
            self.tools.execute("list_files", {"shell": "pwd"})

    def test_unsupported_tool_and_invalid_argument_types_fail_closed(self) -> None:
        cases = (
            ("shell", {}, "unsupported tool"),
            ("list_files", {"prefix": 1}, "prefix must be a string"),
            ("read_file", {"path": ""}, "path must be a non-empty string"),
            (
                "read_file",
                {"path": "tracked.txt", "start_line": True},
                "start_line must be a positive integer",
            ),
            (
                "read_file",
                {"path": "tracked.txt", "start_line": 2, "end_line": 1},
                "end_line must be greater",
            ),
            (
                "search_text",
                {"query": "x" * 513},
                "query must be at most",
            ),
            (
                "search_text",
                {"query": "x", "path": 1},
                "path must be a string",
            ),
            (
                "search_text",
                {"query": "x", "max_results": False},
                "max_results must be a positive integer",
            ),
        )
        for tool, arguments, message in cases:
            with self.subTest(tool=tool, message=message):
                with self.assertRaisesRegex(ToolError, message):
                    self.tools.execute(tool, arguments)

    def test_read_and_search_truncation_and_skipped_files_are_reported(self) -> None:
        (self.repository / "many.txt").write_text(
            "needle " + "x" * 250 + "\nneedle second\n",
            encoding="utf-8",
        )
        (self.repository / "invalid.txt").write_bytes(b"\xff")
        narrow = WorkspaceTools(
            self.repository,
            max_file_bytes=4096,
            max_output_chars=40,
        )

        read = narrow.execute("read_file", {"path": "many.txt"})
        search = narrow.execute(
            "search_text",
            {"query": "needle", "max_results": 1},
        )

        self.assertTrue(read.output["truncated"])
        self.assertTrue(search.output["truncated"])
        self.assertIn("invalid.txt", search.output["skipped_files"])

    def test_write_replace_and_delete_validate_target_state(self) -> None:
        cases = (
            (
                "write_file",
                {
                    "path": "new.txt",
                    "content": "new\n",
                    "expected_sha256": 1,
                },
                "must be a string or null",
            ),
            (
                "write_file",
                {
                    "path": "new.txt",
                    "content": "new\n",
                    "expected_sha256": "sha256:" + "0" * 64,
                },
                "new files require",
            ),
            (
                "replace_text",
                {
                    "path": "missing.txt",
                    "old": "old",
                    "new": "new",
                    "expected_sha256": "sha256:" + "0" * 64,
                },
                "target does not exist",
            ),
            (
                "delete_file",
                {
                    "path": "missing.txt",
                    "expected_sha256": "sha256:" + "0" * 64,
                },
                "target does not exist",
            ),
            (
                "write_file",
                {
                    "path": "binary.txt",
                    "content": "a\0b",
                    "expected_sha256": None,
                },
                "binary content",
            ),
            (
                "write_file",
                {
                    "path": "large.txt",
                    "content": "x" * 5000,
                    "expected_sha256": None,
                },
                "content exceeds",
            ),
        )
        for tool, arguments, message in cases:
            with self.subTest(tool=tool, message=message):
                with self.assertRaisesRegex(ToolError, message):
                    self.tools.execute(tool, arguments)

    def test_nested_symlink_and_non_utf8_targets_are_rejected(self) -> None:
        target = self.repository / "target"
        target.mkdir()
        (self.repository / "linked").symlink_to(target, target_is_directory=True)
        (self.repository / "invalid.txt").write_bytes(b"\xff")

        with self.assertRaisesRegex(ToolError, "traverses a symlink"):
            self.tools.execute(
                "write_file",
                {
                    "path": "linked/new.txt",
                    "content": "new\n",
                    "expected_sha256": None,
                },
            )
        with self.assertRaisesRegex(ToolError, "not UTF-8"):
            self.tools.execute("read_file", {"path": "invalid.txt"})

    def test_git_listing_failures_are_normalized(self) -> None:
        failed = subprocess.CompletedProcess(
            args=["git"],
            returncode=1,
            stdout=b"",
            stderr=b"repository unavailable",
        )
        with patch("sisyphus_harness.tools.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(ToolError, "repository unavailable"):
                self.tools.execute("list_files", {})
            with self.assertRaisesRegex(ToolError, "repository unavailable"):
                self.tools.execute("search_text", {"query": "baseline"})

        with patch(
            "sisyphus_harness.tools.subprocess.run",
            side_effect=subprocess.TimeoutExpired(("git", "ls-files"), 15),
        ):
            with self.assertRaisesRegex(ToolError, "operation timed out"):
                self.tools.execute("list_files", {})

    def test_operator_control_file_is_readable_but_not_mutable(self) -> None:
        config = self.repository / "sisyphus-harness.toml"
        config.write_text("[provider]\nmodel = 'local'\n", encoding="utf-8")
        tools = WorkspaceTools(
            self.repository,
            max_file_bytes=4096,
            max_output_chars=2000,
            protected_write_paths=(config,),
        )
        current = tools.execute(
            "read_file",
            {"path": "sisyphus-harness.toml"},
        )
        self.assertIn("[provider]", current.output["content"])

        for tool, arguments in (
            (
                "write_file",
                {
                    "path": "sisyphus-harness.toml",
                    "content": "changed\n",
                    "expected_sha256": current.output["sha256"],
                },
            ),
            (
                "replace_text",
                {
                    "path": "sisyphus-harness.toml",
                    "old": "local",
                    "new": "remote",
                    "expected_sha256": current.output["sha256"],
                },
            ),
            (
                "delete_file",
                {
                    "path": "sisyphus-harness.toml",
                    "expected_sha256": current.output["sha256"],
                },
            ),
        ):
            with self.subTest(tool=tool):
                with self.assertRaisesRegex(ToolError, "protected from model writes"):
                    tools.execute(tool, arguments)

    def test_protected_write_path_must_be_inside_workspace(self) -> None:
        outside = Path(self.temporary_directory.name) / "outside.toml"
        with self.assertRaisesRegex(ToolError, "outside workspace"):
            WorkspaceTools(
                self.repository,
                max_file_bytes=4096,
                max_output_chars=2000,
                protected_write_paths=(outside,),
            )


if __name__ == "__main__":
    unittest.main()
