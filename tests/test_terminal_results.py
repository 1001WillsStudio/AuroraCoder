"""Exercise the native persistent shell, including the Docker Bash path."""

import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from src.code_sandbox import sandbox, terminal_sessions
from src.code_tools import terminal_runner


def python_command(source):
    argv = [sys.executable, "-c", source]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


class TerminalResultsTest(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.workspace = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(patch.object(sandbox, "WORKSPACE", self.workspace))
        stack.enter_context(patch.object(terminal_runner, "WORKSPACE", self.workspace))
        stack.enter_context(patch.object(tempfile, "gettempdir", return_value=str(self.workspace)))
        stack.enter_context(patch.dict(os.environ, {"AURORACODER_DOCKER": "1", "DEMO": "wrong-directory"}))
        self.shell = sandbox.PersistentShell()
        stack.enter_context(patch.object(terminal_sessions, "shell", self.shell))
        self.addCleanup(self.shell.stop)
        self.shell.start()

    def test_silent_success_and_failure_have_distinct_exit_codes(self):
        for status in (0, 17):
            with self.subTest(status=status):
                output, _ = terminal_runner.run_terminal_cmd_tool({
                    "command": python_command(f"raise SystemExit({status})"),
                })
                self.assertIn("Status: completed", output)
                self.assertIn(f"Exit Code: {status}", output)
                self.assertIn("produced no output", output)

    @unittest.skipIf(os.name == "nt", "Exercises Bash syntax and shell-local variables.")
    def test_multiline_commands_preserve_output_and_shell_state(self):
        result = self.shell.run("export AURORA_SHELL_TEST=kept\ncat <<'EOF'\nline one\nEOF\nfalse;")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.output, "line one\n")
        self.assertEqual(self.shell.run('printf "%s" "$AURORA_SHELL_TEST"').output, "kept")

    def assert_finishes(self, result):
        self.assertEqual(result.status, "running")
        self.assertIsNone(result.exit_code)
        self.assertIsNotNone(result.status_path)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                if result.status_path.read_text().strip() == "7":
                    break
            except FileNotFoundError:
                pass
            time.sleep(0.04)
        else:
            self.fail("The background command did not record exit status 7")
        self.assertIn("finished", result.output_path.read_text())

    @unittest.skipIf(os.name == "nt", "The Docker Bash shell supports detached background jobs.")
    def test_explicit_background_command_reports_its_final_exit_code(self):
        result = self.shell.run(python_command("print('finished'); raise SystemExit(7)"), blocking=False)
        self.assert_finishes(result)

    def test_timeout_leaves_progress_files_and_allows_next_command(self):
        command = python_command("import time; time.sleep(0.7); print('finished'); raise SystemExit(7)")
        result = self.shell.run(command, timeout=0.1)
        second = self.shell.run(python_command("print('new shell')"))
        self.assertEqual(second.exit_code, 0)
        self.assertIn("new shell", second.output)
        self.assert_finishes(result)

    def test_foreground_output_is_bounded_before_decoding(self):
        command = python_command("import sys; print('BEGIN ' + '它' * 600_000 + ' END🌕', file=sys.stderr)")
        with patch.object(Path, "read_text", side_effect=AssertionError("Unbounded terminal log read")):
            result = self.shell.run(command, output_limit=64)
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.output.startswith("BEGIN 它"))
        self.assertTrue(result.output.endswith(" END🌕\n"))
        self.assertIn("1,800,015 bytes of output; middle truncated", result.output)
        self.assertNotIn("�", result.output)
        self.assertLess(len(result.output), 145)

    def test_native_terminal_runs_in_the_requested_working_directory(self):
        target = self.workspace / "nested %DEMO% folder's worktree"
        target.mkdir()
        command = python_command("from pathlib import Path; print(Path.cwd().name)")
        output, _ = terminal_runner.run_terminal_cmd_tool({"command": command, "cwd": target.name})
        self.assertIn("Exit Code: 0", output)
        self.assertIn(target.name, output)
        again, _ = terminal_runner.run_terminal_cmd_tool({"command": command})
        self.assertIn(target.name, again)

    def test_invalid_working_directory_does_not_run_the_command(self):
        command = python_command("from pathlib import Path; Path('must-not-run.txt').touch()")
        output, _ = terminal_runner.run_terminal_cmd_tool({"command": command, "cwd": "no-such-worktree"})
        self.assertIn("Error:", output)
        self.assertFalse((self.workspace / "must-not-run.txt").exists())

    @unittest.skipIf(os.name == "nt", "The Docker Bash shell supports detached background jobs.")
    def test_detached_directory_does_not_relocate_the_parent_shell(self):
        target = self.workspace / "detached workspace"
        target.mkdir()
        command = python_command(
            "from pathlib import Path; Path('marker').touch(); print('finished'); raise SystemExit(7)"
        )
        self.assert_finishes(self.shell.run(command, cwd=target, blocking=False))
        self.assertTrue((target / "marker").exists())
        self.assertEqual(self.shell.run("pwd").output.strip(), str(self.workspace))
