from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from sisyphus_harness.models import CommandSpec
from sisyphus_harness.verifier import BoundedVerifier, VerificationError

from .helpers import create_git_repo


def command(
    name: str,
    code: str,
    *,
    timeout: float = 5,
    arguments: tuple[str, ...] = (),
    criteria: tuple[str, ...] = ("command succeeds",),
) -> CommandSpec:
    return CommandSpec(
        name=name,
        argv=(sys.executable, "-c", code, *arguments),
        timeout_seconds=timeout,
        criteria=criteria,
    )


class VerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        root = Path(self.temporary_directory.name)
        self.repository = create_git_repo(root / "repository")
        self.artifacts = root / "artifacts"
        self.verifier = BoundedVerifier(self.artifacts)

    def test_success_receipt_records_cwd_output_executable_and_criteria(self) -> None:
        receipt = self.verifier.verify(
            self.repository,
            (
                command(
                    "cwd",
                    "import os; print(os.getcwd()); print('x' * 10000)",
                    criteria=("runs in worktree", "preserves full output"),
                ),
            ),
            run_id="success",
        )

        self.assertTrue(receipt.passed)
        self.assertTrue(receipt.workspace_unchanged)
        result = receipt.commands[0]
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.executable_sha256.startswith("sha256:"))
        self.assertIsNone(result.failure_category)
        stdout = (self.artifacts / "success" / result.stdout_path).read_text(
            encoding="utf-8"
        )
        self.assertIn(str(self.repository.resolve()), stdout)
        self.assertIn("x" * 10000, stdout)
        payload = json.loads(
            (self.artifacts / "success" / "receipt.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            payload["criteria"],
            [
                {
                    "command_name": "cwd",
                    "criterion": "runs in worktree",
                    "passed": True,
                },
                {
                    "command_name": "cwd",
                    "criterion": "preserves full output",
                    "passed": True,
                },
            ],
        )
        self.assertEqual(payload["worktree_commit_sha"], receipt.worktree_commit_sha)

    def test_shell_metacharacters_are_passed_as_plain_arguments(self) -> None:
        marker = self.repository / "should-not-exist"
        raw = f"; touch {marker}"
        receipt = self.verifier.verify(
            self.repository,
            (
                command(
                    "no-shell",
                    "import sys; print(sys.argv[1])",
                    arguments=(raw,),
                ),
            ),
            run_id="no-shell",
        )

        self.assertTrue(receipt.passed)
        self.assertFalse(marker.exists())
        output = (
            self.artifacts
            / "no-shell"
            / receipt.commands[0].stdout_path
        ).read_text(encoding="utf-8")
        self.assertEqual(output.strip(), raw)

    def test_timeout_kills_command_and_fails_receipt(self) -> None:
        receipt = self.verifier.verify(
            self.repository,
            (command("slow", "import time; time.sleep(30)", timeout=0.1),),
            run_id="timeout",
        )

        self.assertFalse(receipt.passed)
        self.assertTrue(receipt.commands[0].timed_out)
        self.assertEqual(receipt.commands[0].failure_category, "timeout")
        self.assertLess(receipt.commands[0].duration_ms, 5000)

    def test_command_mutation_invalidates_verification(self) -> None:
        receipt = self.verifier.verify(
            self.repository,
            (
                command(
                    "mutates",
                    "from pathlib import Path; Path('tracked.txt').write_text('changed\\n')",
                ),
            ),
            run_id="mutation",
        )

        self.assertFalse(receipt.passed)
        self.assertFalse(receipt.workspace_unchanged)
        self.assertFalse(receipt.commands[0].workspace_unchanged)
        self.assertEqual(
            receipt.commands[0].failure_category,
            "workspace_mutation",
        )

    def test_missing_executable_is_a_persisted_failure(self) -> None:
        spec = CommandSpec(
            name="missing",
            argv=("definitely-not-a-real-sisyphus-executable",),
            timeout_seconds=1,
            criteria=("tool is installed",),
        )
        receipt = self.verifier.verify(
            self.repository,
            (spec,),
            run_id="missing",
        )

        self.assertFalse(receipt.passed)
        self.assertIsNone(receipt.commands[0].exit_code)
        self.assertIn("not found", receipt.commands[0].error)
        self.assertEqual(receipt.commands[0].failure_category, "launch_error")
        self.assertTrue((self.artifacts / "missing" / "receipt.json").is_file())

    def test_failure_category_distinguishes_assertion_and_execution_errors(self) -> None:
        assertion = self.verifier.verify(
            self.repository,
            (command("assertion", "assert False, 'hidden assertion detail'"),),
            run_id="assertion-failure",
        )
        execution = self.verifier.verify(
            self.repository,
            (command("execution", "print(missing_hidden_name)"),),
            run_id="execution-error",
        )

        self.assertEqual(assertion.commands[0].failure_category, "assertion_failure")
        self.assertEqual(execution.commands[0].failure_category, "execution_error")

    def test_zero_commands_and_duplicate_names_are_rejected(self) -> None:
        with self.assertRaisesRegex(VerificationError, "at least one"):
            self.verifier.verify(self.repository, (), run_id="empty")
        duplicate = command("same", "pass")
        with self.assertRaisesRegex(VerificationError, "names must be unique"):
            self.verifier.verify(
                self.repository,
                (duplicate, duplicate),
                run_id="duplicate",
            )

    def test_run_id_cannot_escape_artifact_root(self) -> None:
        with self.assertRaisesRegex(VerificationError, "unsafe"):
            self.verifier.verify(
                self.repository,
                (command("ok", "pass"),),
                run_id="../escape",
            )

    def test_missing_workspace_and_duplicate_run_are_rejected(self) -> None:
        with self.assertRaisesRegex(VerificationError, "workspace does not exist"):
            self.verifier.verify(
                self.repository / "missing",
                (command("ok", "pass"),),
                run_id="missing-workspace",
            )

        self.verifier.verify(
            self.repository,
            (command("ok", "pass"),),
            run_id="same-run",
        )
        with self.assertRaisesRegex(VerificationError, "already exists"):
            self.verifier.verify(
                self.repository,
                (command("ok", "pass"),),
                run_id="same-run",
            )

    def test_relative_executable_and_non_file_are_handled(self) -> None:
        executable = self.repository / "check.py"
        executable.write_text("#!/usr/bin/env python3\nprint('ok')\n", encoding="utf-8")
        executable.chmod(0o755)
        success = self.verifier.verify(
            self.repository,
            (
                CommandSpec(
                    name="relative executable",
                    argv=("./check.py",),
                    timeout_seconds=2,
                    criteria=("relative executable runs",),
                ),
            ),
            run_id="relative-executable",
        )
        self.assertTrue(success.passed)
        self.assertEqual(
            success.commands[0].executable_path,
            str(executable.resolve()),
        )

        failure = self.verifier.verify(
            self.repository,
            (
                CommandSpec(
                    name="directory",
                    argv=("./.git",),
                    timeout_seconds=2,
                    criteria=("target is executable",),
                ),
            ),
            run_id="non-file-executable",
        )
        self.assertFalse(failure.passed)
        self.assertIn("not a file", failure.commands[0].error)

    def test_process_launch_error_is_persisted(self) -> None:
        real_popen = subprocess.Popen

        def fail_verification_launch(*args, **kwargs):
            argv = args[0]
            if argv and argv[0] == "git":
                return real_popen(*args, **kwargs)
            raise OSError("launch denied")

        with patch(
            "sisyphus_harness.verifier.subprocess.Popen",
            side_effect=fail_verification_launch,
        ):
            receipt = self.verifier.verify(
                self.repository,
                (command("launch", "pass"),),
                run_id="launch-error",
            )

        self.assertFalse(receipt.passed)
        self.assertIsNone(receipt.commands[0].exit_code)
        self.assertIn("launch denied", receipt.commands[0].error)
        stderr = (
            self.artifacts
            / "launch-error"
            / receipt.commands[0].stderr_path
        ).read_text(encoding="utf-8")
        self.assertIn("failed to start", stderr)


if __name__ == "__main__":
    unittest.main()
