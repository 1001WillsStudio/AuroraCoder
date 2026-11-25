"""
Docker-first sandbox: workspace path + persistent shell.

Replaces the heavyweight ``session_manager`` for the Docker deployment model
where the workspace is a fixed directory (``/workspace``) and the Python
environment is the system Python (Docker) or conda (host: ``agent``).

Usage::

    from src.code_sandbox import WORKSPACE, shell

    # Workspace path
    files = list(WORKSPACE.iterdir())

    # Run a command
    result = shell.run("ls -la", timeout=30)

    # Restart the shell
    shell.restart()
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from ..config import WORKSPACE_DIR, DEFAULT_BASE_ENV_NAME, TERMINAL_MAX_OUTPUT_CHARS
from .terminal_jobs import job_paths, remember_terminal_job
from .terminal_output import read_terminal_output

logger = logging.getLogger(__name__)
_CMD_PERCENT_ENV = "_AURORA_LITERAL_PERCENT"

# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------

WORKSPACE: Path = Path(WORKSPACE_DIR) if WORKSPACE_DIR else Path.cwd()
"""The agent's working directory.  ``/workspace`` in Docker, cwd otherwise."""


def get_workspace() -> Path:
    """Return the workspace directory, creating it if needed."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    return WORKSPACE


# ---------------------------------------------------------------------------
# Python / Environment helpers
# ---------------------------------------------------------------------------

def get_python_path() -> Optional[Path]:
    """Return the Python executable for the sandbox environment.

    In Docker, ``sys.executable`` is the canonical Python. On the host a conda
    lookup may be used.  Falls back to ``sys.executable``.
    """

    # Fast path: if we're already running inside the target env, just use it
    if DEFAULT_BASE_ENV_NAME and DEFAULT_BASE_ENV_NAME in (sys.executable or ""):
        return Path(sys.executable)

    # Lookup via conda
    if not DEFAULT_BASE_ENV_NAME:
        return Path(sys.executable)

    try:
        result = subprocess.run(
            ["conda", "info", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return Path(sys.executable)

        info = json.loads(result.stdout)
        for env_dir in info.get("envs_dirs", []):
            candidate = Path(env_dir) / DEFAULT_BASE_ENV_NAME / "bin" / "python"
            if candidate.exists():
                return candidate
    except Exception:
        pass

    return Path(sys.executable)


def get_conda_env_path() -> Optional[Path]:
    """Return the Python environment directory (conda)."""

    if not DEFAULT_BASE_ENV_NAME:
        return None
    try:
        result = subprocess.run(
            ["conda", "info", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        info = json.loads(result.stdout)
        for env_dir in info.get("envs_dirs", []):
            p = Path(env_dir) / DEFAULT_BASE_ENV_NAME
            if p.exists():
                return p
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Persistent Shell
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ShellResult:
    status: Literal["completed", "running", "error"]
    output: str = ""
    exit_code: int | None = None
    detail: str = ""
    output_path: Path | None = None
    status_path: Path | None = None
    job_id: str | None = None


class PersistentShell:
    """A long-lived bash (or cmd) subprocess that preserves state across commands.

    Environment variables, working directory, and shell history persist
    between ``run()`` calls.  The shell is started in ``WORKSPACE`` with
    the sandbox environment activated.
    """

    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()

    # -- lifecycle ----------------------------------------------------------

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        """Start (or restart) the persistent shell."""
        with self._lock:
            self._start()

    def _start(self) -> None:
        self.stop()

        shell_cmd = ["cmd.exe", "/D", "/V:OFF"] if sys.platform == "win32" else ["bash", "-i"]
        env = {**os.environ, _CMD_PERCENT_ENV: "%"} if sys.platform == "win32" else None
        cwd = str(get_workspace())

        try:
            self._proc = subprocess.Popen(
                shell_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=cwd,
                env=env,
                shell=False,
                errors="replace",
                bufsize=1,
                universal_newlines=True,
            )
            # Activate the environment inside the shell
            activate = self._activation_command()
            if activate:
                self._init_command(activate)
            logger.info("Persistent shell started (cwd=%s)", cwd)
        except Exception as e:
            logger.error("Failed to start persistent shell: %s", e)
            self._proc = None

    def stop(self) -> None:
        with self._lock:
            proc = self._proc
            if proc is None:
                return
            self._proc = None
            try:
                proc.stdin.close()
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            finally:
                proc.stdout.close()

    def restart(self) -> str:
        """Stop and re-start the shell.  Returns a status message."""
        logger.info("Restarting persistent shell...")
        self.start()
        return "Persistent shell has been restarted."

    # -- command execution --------------------------------------------------

    def run(
        self,
        command: str,
        timeout: int = 120,
        blocking: bool = True,
        output_limit: int = TERMINAL_MAX_OUTPUT_CHARS,
        cwd: Path | None = None,
    ) -> ShellResult:
        """Run once and return a completion code or the live command's output and status files."""
        with self._lock:
            return self._run(command, timeout, blocking, output_limit, cwd)

    def _run(self, command: str, timeout: int, blocking: bool, output_limit: int, cwd: Path | None) -> ShellResult:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return ShellResult("error", detail="Persistent shell not running.")

        command = self._strip_background_syntax(command)
        if cwd is not None:
            if sys.platform == "win32":
                path = str(cwd).replace("%", f"%{_CMD_PERCENT_ENV}%")
                command = f'cd /d "{path}" && (\n{command}\n)'
            else:
                command = f"builtin cd -- {self._shell_quote(str(cwd))} && {{\n{command}\n}}"
        cmd_id = uuid.uuid4().hex
        boundary = f"END_{cmd_id}"
        exit_var = f"_aurora_exit_{cmd_id}"
        background = not blocking and sys.platform != "win32"
        output_path, status_path = job_paths(cmd_id, background)
        if background:
            output_path.parent.mkdir(exist_ok=True)
        quote = self._shell_quote

        if background:
            script = 'bash -c "$1"; code=$?; printf "%s\n" "$code" > "$2"; exit "$code"'
            wrapped = (
                f"nohup bash -c {quote(script)} aurora {quote(command)} {quote(str(status_path))}"
                f" > {quote(str(output_path))} 2>&1 &\n"
                f"printf '{boundary}:%s\\n' \"$!\"\n"
            )
        elif sys.platform == "win32":
            # Capture ERRORLEVEL on a separate line, before the next command runs.
            wrapped = (
                'cmd.exe /D /C exit 0\n'
                f'({command}) > "{output_path}" 2>&1\n'
                f'set "{exit_var}=%ERRORLEVEL%"\n'
                f'> "{status_path}" echo %{exit_var}%\n'
                f'echo {boundary}:%{exit_var}%\n'
                f'set "{exit_var}="\n'
            )
        else:
            # Evaluate in the existing shell to retain cwd and environment changes.
            # Pass the entire command intact, including heredocs and trailing semicolons.
            wrapped = (
                f"builtin eval -- {quote(command)} > {quote(str(output_path))} 2>&1\n"
                f"{exit_var}=$?\n"
                f'printf "%s\\n" "${exit_var}" > {quote(str(status_path))}\n'
                f'printf "{boundary}:%s\\n" "${exit_var}"\n'
                f"unset {exit_var}\n"
            )

        try:
            proc.stdin.write(wrapped)
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            return ShellResult("error", detail=f"Failed to write to shell: {exc}")

        boundary_pattern = re.compile(rf"{boundary}:(-?\d+)\s*$")
        numbers = []

        def read_status():
            for line in proc.stdout:
                match = boundary_pattern.search(line)
                if match:
                    numbers.append(int(match.group(1)))
                    return

        reader = threading.Thread(target=read_status, daemon=True)
        reader.start()
        reader.join(timeout=timeout)

        if reader.is_alive():
            logger.warning("Command still running after %ss, replacing the foreground shell", timeout)
            self._proc = None
            # The old shell drains its submitted command and exits on EOF.
            proc.stdin.close()

            def reap():
                reader.join()
                proc.stdout.close()
                proc.wait()

            threading.Thread(target=reap, daemon=True).start()
            self._start()
            return ShellResult(
                "running",
                detail=(
                    f"Command is still running after {timeout}s (not killed) — continuing in background.\n"
                    f"Terminal PID: {proc.pid}"
                ),
                output_path=output_path, status_path=status_path,
                job_id=None if background else remember_terminal_job(cmd_id, False, proc.pid),
            )

        if not numbers:
            return ShellResult("error", detail="The shell closed before reporting command status.",
                               output_path=output_path)
        if background:
            return ShellResult("running", detail=f"Background PID: {numbers[0]}",
                               output_path=output_path, status_path=status_path,
                               job_id=remember_terminal_job(cmd_id, True, numbers[0]))

        try:
            output = read_terminal_output(output_path, output_limit)
        except OSError as exc:
            output = f"[Error reading command output: {exc}]"
        for path in (output_path, status_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove finished command file %s", path)
        return ShellResult("completed", output=output, exit_code=numbers[0])

    # -- internals ----------------------------------------------------------

    def _activation_command(self) -> str:
        # Docker: system Python, no activation needed
        if os.environ.get("AURORACODER_DOCKER"):
            return ""

        # Host / conda path
        if not DEFAULT_BASE_ENV_NAME:
            return ""
        if sys.platform == "win32":
            return f"conda activate {DEFAULT_BASE_ENV_NAME}"
        return (
            f"source $(conda info --base)/etc/profile.d/conda.sh && "
            f"conda activate {DEFAULT_BASE_ENV_NAME}"
        )

    def _init_command(self, command: str, timeout: int = 30) -> None:
        """Run a state-changing init command (e.g. conda activate).

        Reads stdout line-by-line until the boundary marker appears or
        *timeout* seconds elapse.  A hung init command no longer blocks
        :meth:`start` indefinitely.
        """
        if not self._proc or not command:
            return
        boundary = f"INIT_{uuid.uuid4().hex[:8]}"
        self._proc.stdin.write(f"{command}\necho {boundary}\n")
        self._proc.stdin.flush()

        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            line = self._proc.stdout.readline()
            if not line or boundary in line:
                return

        logger.warning(
            "Init command did not produce boundary within %ds: %.60s...",
            timeout, command,
        )

    @staticmethod
    def _strip_background_syntax(command: str) -> str:
        cmd = command.strip()
        cmd = re.sub(r"^nohup\s+", "", cmd)
        cmd = re.sub(r"(?<!&)&\s*$", "", cmd)
        return cmd.strip()

    @staticmethod
    def _shell_quote(s: str) -> str:
        return "'" + s.replace("'", "'\\''") + "'"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

shell = PersistentShell()
"""The global persistent shell instance."""
