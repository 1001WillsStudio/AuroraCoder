import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.code_sandbox import sandbox, terminal_sessions
from src.tool_definitions import execute_tool_call


@unittest.skipIf(sys.platform == "win32", "Tests the Docker Bash terminal")
class ConversationTerminalTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        (self.root / "fork").mkdir()
        workspace = patch.object(sandbox, "get_workspace", return_value=self.root)
        workspace.start()
        self.addCleanup(workspace.stop)
        activate = patch.object(sandbox.PersistentShell, "_activation_command", return_value="")
        activate.start()
        self.addCleanup(activate.stop)
        self.addCleanup(terminal_sessions.stop_terminal_sessions)

    def run_terminal(self, conversation_id, command, **args):
        applied, result = execute_tool_call(
            "run_terminal_command", {"command": command, **args}, conversation_id=conversation_id,
        )
        self.assertNotIn("conversation_id", applied)
        return result

    def test_two_conversations_keep_separate_cwd_and_exports(self):
        self.run_terminal("chat-a", "cd fork && export AC_SESSION_MARK=alpha")
        self.run_terminal("chat-b", "export AC_SESSION_MARK=beta")
        command = 'printf "%s %s" "$PWD" "$AC_SESSION_MARK"'
        self.assertIn(f"{self.root / 'fork'} alpha", self.run_terminal("chat-a", command))
        self.assertIn(f"{self.root} beta", self.run_terminal("chat-b", command))

        clean_a = self.run_terminal("chat-a", "pwd", new_terminal=True)
        self.assertIn(f"STDOUT:\n{self.root}", clean_a)
        self.assertNotIn(os.fspath(self.root / "fork"), clean_a)
        self.assertIn(f"{self.root} beta", self.run_terminal("chat-b", command))

    def test_idle_session_requires_explicit_reset_before_running_in_a_fresh_cwd(self):
        with patch.object(terminal_sessions, "monotonic", return_value=1):
            self.run_terminal("chat-a", "cd fork")
        with patch.object(terminal_sessions, "monotonic", return_value=10000):
            error = self.run_terminal("chat-a", "touch should-not-run")
            self.assertIn("expired", error)
            self.assertFalse((self.root / "should-not-run").exists())
            self.assertIn(f"STDOUT:\n{self.root}", self.run_terminal("chat-a", "pwd", new_terminal=True))


if __name__ == "__main__":
    unittest.main()
