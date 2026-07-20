from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
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

    def test_global_deadline_clamps_command_timeout(self) -> None:
        started = time.monotonic()
        receipt = self.verifier.verify(
            self.repository,
            (command("deadline", "import time; time.sleep(30)", timeout=10),),
            run_id="global-deadline",
            deadline_monotonic=started + 0.1,
        )

        self.assertFalse(receipt.passed)
        self.assertTrue(receipt.commands[0].timed_out)
        self.assertLess(time.monotonic() - started, 3)

    @unittest.skipIf(os.name == "nt", "POSIX process groups are required")
    def test_active_same_group_descendant_fails_and_is_killed(self) -> None:
        late_marker = self.repository / "late-descendant-write"
        child_code = (
            "import sys,time; from pathlib import Path; "
            "time.sleep(0.6); Path(sys.argv[1]).write_text('late')"
        )
        parent_code = (
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]], "
            "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
            "stderr=subprocess.DEVNULL, close_fds=True)"
        )

        started = time.monotonic()
        receipt = self.verifier.verify(
            self.repository,
            (
                command(
                    "descendant",
                    parent_code,
                    arguments=(child_code, str(late_marker)),
                ),
            ),
            run_id="active-descendant",
        )

        self.assertFalse(receipt.passed)
        self.assertEqual(receipt.commands[0].exit_code, 0)
        self.assertEqual(receipt.commands[0].failure_category, "process_leak")
        self.assertIn("descendant processes", receipt.commands[0].error)
        self.assertLess(time.monotonic() - started, 3)
        time.sleep(0.8)
        self.assertFalse(late_marker.exists())

    @unittest.skipIf(os.name == "nt", "POSIX session isolation is required")
    def test_open_pipe_from_detached_descendant_fails_closed_without_hanging(self) -> None:
        parent_code = (
            "import subprocess,sys; "
            "child=subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(30)'], start_new_session=True); "
            "print(child.pid, flush=True)"
        )
        started = time.monotonic()
        receipt = self.verifier.verify(
            self.repository,
            (command("detached-pipe", parent_code),),
            run_id="detached-open-pipe",
        )

        result = receipt.commands[0]
        stdout = self.artifacts / "detached-open-pipe" / result.stdout_path
        child_pid = int(stdout.read_text(encoding="utf-8").strip())
        self.addCleanup(_best_effort_kill, child_pid)
        self.assertFalse(receipt.passed)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.failure_category, "process_leak")
        self.assertLess(time.monotonic() - started, 3)

    def test_combined_output_limit_kills_command_and_bounds_artifacts(self) -> None:
        verifier = BoundedVerifier(self.artifacts, max_output_bytes=1024)
        receipt = verifier.verify(
            self.repository,
            (
                command(
                    "noisy",
                    "import sys; sys.stdout.write('x' * 100000); "
                    "sys.stderr.write('y' * 100000)",
                ),
            ),
            run_id="output-limit",
        )

        result = receipt.commands[0]
        stdout = self.artifacts / "output-limit" / result.stdout_path
        stderr = self.artifacts / "output-limit" / result.stderr_path
        self.assertFalse(receipt.passed)
        self.assertEqual(result.failure_category, "output_limit")
        self.assertLessEqual(stdout.stat().st_size + stderr.stat().st_size, 1024)

    def test_thread_output_capture_fallback_records_complete_output(self) -> None:
        with patch("sisyphus_harness.verifier._USE_THREAD_CAPTURE", True):
            receipt = self.verifier.verify(
                self.repository,
                (command("thread-capture", "print('thread output')"),),
                run_id="thread-capture",
            )

        result = receipt.commands[0]
        output = (self.artifacts / "thread-capture" / result.stdout_path).read_text(
            encoding="utf-8"
        )
        self.assertTrue(receipt.passed)
        self.assertEqual(output.strip(), "thread output")

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


def _best_effort_kill(process_id: int) -> None:
    try:
        os.kill(process_id, signal.SIGKILL)
    except ProcessLookupError:
        pass


if __name__ == "__main__":
    unittest.main()
