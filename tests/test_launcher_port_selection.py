import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "dev-scripts"
PORTS = (
    "BACKEND_PORT", "FRONTEND_PORT", "VNC_PORT", "TOOLSTORE_PORT",
    "DEV_PORT_START", "DEV_PORT_END",
)


class LauncherPortSelectionTest(unittest.TestCase):
    def test_fallback_ports_skip_listeners_and_ports_already_chosen_for_this_launch(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            occupied = listener.getsockname()[1]
            environment = {**os.environ, **dict.fromkeys(PORTS, str(occupied))}
            environment["DEV_PORT_END"] = str(occupied + 2)
            output = self.resolve_ports(environment)

        line = next(line for line in output.splitlines() if line.startswith("Selected="))
        backend, frontend, vnc, tools, start, end = map(int, line.removeprefix("Selected=").split())
        self.assertEqual(end - start, 2)
        assigned = [backend, frontend, vnc, tools, *range(start, end + 1)]
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertTrue(all(port > occupied for port in assigned), assigned)

    def resolve_ports(self, environment):
        if sys.platform != "win32":
            values = " ".join(f'${name}' for name in PORTS)
            return subprocess.check_output(
                ["bash", "-ec", f'source "$1"; resolve_auroracoder_ports; echo "Selected={values}"',
                 "bash", SCRIPTS / "port-availability.sh"],
                env=environment, text=True, timeout=15,
            )

        values = " ".join(f"%{name}%" for name in PORTS)
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "ports-test.bat"
            wrapper.write_text(
                f'@echo off\ncall "{SCRIPTS / "port-availability.bat"}"\n'
                f'if errorlevel 1 exit /b 1\necho Selected={values}\n',
                encoding="utf-8",
            )
            return subprocess.check_output(
                ["cmd.exe", "/d", "/c", wrapper],
                env=environment, text=True, timeout=15,
            )


if __name__ == "__main__":
    unittest.main()
