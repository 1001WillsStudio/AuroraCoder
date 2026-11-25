import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("git"), "Git is required for the credential protocol test")
class GitCredentialTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.temp = Path(folder.name)
        self.config = self.temp / "git-settings"
        self.settings = self.temp / "settings.json"
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": str(self.config), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_TERMINAL_PROMPT": "0", "GITHUB_TOKEN": "TEST_ENV_CREDENTIAL"}
        self.env.pop("GITHUB_API_KEY", None)

    def call(self, *args, text_in=None, check=True):
        return subprocess.run(
            args, cwd=self.temp, env=self.env, input=text_in, text=True, capture_output=True,
            timeout=15, check=check,
        )

    def configure(self):
        return self.call(sys.executable, str(ROOT / "src" / "git_credentials.py"),
                         str(self.settings), "configure")

    def test_local_git_gets_rotated_credentials_while_configuration_has_no_token(self):
        self.call("git", "config", "--global",
                  "url.https://oauth2:TEST_LEGACY_CREDENTIAL@github.com/.insteadOf", "https://github.com/")
        self.call("git", "config", "--global",
                  "url.https://mirror.example/.insteadOf", "https://mirror.local/")
        self.configure()
        self.configure()
        config = self.config.read_text(encoding="utf-8")
        self.assertNotIn("TEST_LEGACY_CREDENTIAL", config)
        self.assertNotIn("TEST_ENV_CREDENTIAL", config)
        self.assertIn("mirror.example", config)
        self.assertEqual(config.count("git_credentials.py"), 1)

        github = "protocol=https\nhost=github.com\n\n"
        fill = self.call("git", "credential", "fill", text_in=github).stdout
        self.assertIn("password=TEST_ENV_CREDENTIAL", fill)
        self.settings.write_text(json.dumps({"api_keys": {"github": "TEST_ROTATED_KEY"}}), encoding="utf-8")
        fill = self.call("git", "credential", "fill", text_in=github).stdout
        self.assertIn("password=TEST_ROTATED_KEY", fill)
        external = self.call("git", "credential", "fill",
                             text_in="protocol=https\nhost=example.test\n\n", check=False)
        self.assertNotIn("TEST_ROTATED_KEY", external.stdout)
        self.assertNotIn("TEST_ROTATED_KEY", self.config.read_text(encoding="utf-8"))
