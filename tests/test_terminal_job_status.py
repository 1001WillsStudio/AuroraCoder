import os
import re
import sys
import tempfile
import unittest
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from src.code_sandbox import sandbox, terminal_jobs
from src.code_tools import terminal_runner
from src.tool_definitions import execute_tool_call


@unittest.skipUnless(sys.platform == "linux", "Exercises the Docker terminal and Linux process identity")
class TerminalJobStatusTest(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        workspace = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(patch.object(sandbox, "WORKSPACE", workspace))
        stack.enter_context(patch.object(tempfile, "gettempdir", return_value=str(workspace)))
        stack.enter_context(patch.dict(os.environ, {"AURORACODER_DOCKER": "1"}))
        shell = sandbox.PersistentShell()
        stack.enter_context(patch.object(terminal_runner, "shell", shell))
        self.addCleanup(shell.stop)
        shell.start()

    def test_normal_terminal_issues_a_job_that_can_be_waited_for_without_rerunning(self):
        _, launched = execute_tool_call("run_terminal_command", {
            "command": "printf 'started\\n'; sleep 1; printf 'finished\\n'; false",
            "blocking": False,
        })
        job = re.search(r"Job ID: ([a-f0-9]{32})", launched).group(1)

        _, current = execute_tool_call("terminal_job_status", {"job_id": job})
        self.assertIn("Status: running", current)
        _, final = execute_tool_call("terminal_job_status", {"job_id": job, "wait_seconds": 3})
        self.assertIn("Status: completed", final)
        self.assertIn("Exit Code: 1", final)
        self.assertEqual(final.count("started"), 1)
        self.assertEqual(final.count("finished"), 1)

    def test_missing_and_exited_jobs_are_distinct_from_running(self):
        unknown = uuid.uuid4().hex
        self.assertIn("Status: unknown", terminal_jobs.terminal_job_status(unknown))
        job = uuid.uuid4().hex
        terminal_jobs.remember_terminal_job(job, False, 2**31 - 1)
        self.assertIn("ended without a recorded exit code", terminal_jobs.terminal_job_status(job))
        self.assertIn("Error: wait_seconds", terminal_jobs.terminal_job_status(job, 31))
