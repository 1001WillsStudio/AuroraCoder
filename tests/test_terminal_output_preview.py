from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.code_sandbox.terminal_output import read_terminal_output


class OutputPreviewTest(TestCase):
    def test_short_utf8_log_retains_text_and_newlines(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "short.log"
            path.write_bytes("你好\r\n🌕".encode("utf-8"))
            self.assertEqual(read_terminal_output(path, 30), "你好\n🌕")

    def test_small_limit_keeps_only_requested_text_and_metadata(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "small.log"
            path.write_text("ABCD", encoding="utf-8")
            self.assertEqual(read_terminal_output(path, 1), "A\n\n... [3 characters truncated] ...\n\n")
