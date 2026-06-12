"""
Docker-first sandbox: workspace path + persistent shell.

Replaces the heavyweight ``session_manager`` for the Docker deployment model
where the workspace is a fixed directory (``/workspace``) and the conda
environment is pre-built (``agent``).

Usage::

    from src.code_sandbox import WORKSPACE, shell

    # Workspace path
    files = list(WORKSPACE.iterdir())

    # Run a command
    stdout, err = shell.run("ls -la", timeout=30)

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
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Optional, Tuple

from ..config import WORKSPACE_DIR, DEFAULT_BASE_ENV_NAME

logger = logging.getLogger(__name__)

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
# Python / Conda helpers
# ---------------------------------------------------------------------------

def get_python_path() -> Optional[Path]:
    """Return the Python executable for the sandbox conda environment.

    In Docker the *agent* env is already active, so ``sys.executable``
    is usually correct.  Falls back to a conda-info lookup.
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
    """Return the conda environment directory (e.g. ``/opt/conda/envs/agent``)."""
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

class PersistentShell:
    """A long-lived bash (or cmd) subprocess that preserves state across commands.

    Environment variables, working directory, and shell history persist
    between ``run()`` calls.  The shell is started in ``WORKSPACE`` with
    the sandbox conda environment activated.
    """

    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None

    # -- lifecycle ----------------------------------------------------------

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        """Start (or restart) the persistent shell."""
        self.stop()

        shell_cmd = ["cmd.exe", "/D"] if sys.platform == "win32" else ["bash", "-i"]
        cwd = str(get_workspace())

        try:
            self._proc = subprocess.Popen(
                shell_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=cwd,
                shell=False,
                bufsize=1,
                universal_newlines=True,
            )
            # Activate the conda env inside the shell
            activate = self._activation_command()
            if activate:
                self._init_command(activate)
            logger.info("Persistent shell started (cwd=%s)", cwd)
        except Exception as e:
            logger.error("Failed to start persistent shell: %s", e)
            self._proc = None

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None

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
    ) -> Tuple[str, str]:
        """Execute *command* and return ``(stdout, error_message)``.

        When *blocking* is False the command is launched with ``nohup`` in the
        background; a log-file path is returned immediately.
        """
        if not self.is_alive:
            return "", "Persistent shell not running."

        command = self._strip_background_syntax(command)
        cmd_id = uuid.uuid4().hex[:8]
        out_file = os.path.join(tempfile.gettempdir(), f"shell_out_{cmd_id}.txt")
        boundary = f"END_{cmd_id}"

        if not blocking and sys.platform != "win32":
            log_dir = os.path.join(tempfile.gettempdir(), "agent_bg_logs")
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, f"bg_{cmd_id}.log")
            wrapped = (
                f"nohup bash -c {self._shell_quote(command)}"
                f' > "{log_file}" 2>&1 &\n'
                f'echo "Background PID: $!  Log: {log_file}" > "{out_file}"\n'
                f"echo {boundary}\n"
            )
        elif sys.platform == "win32":
            wrapped = (
                f'({command}) > "{out_file}" 2>&1\n'
                f"echo {boundary}\n"
            )
        else:
            # Use ': ;' instead of ';' before '}' — interactive bash (bash -i)
            # rejects bare '; }' as a syntax error after heredocs.  The ':'
            # builtin is a POSIX no-op that satisfies the parser requirement.
            wrapped = (
                f'{{ {command}; : }} > "{out_file}" 2>&1\n'
                f"echo {boundary}\n"
            )

        try:
            self._proc.stdin.write(wrapped)
            self._proc.stdin.flush()
        except Exception as e:
            return "", f"Failed to write to shell: {e}"

        found = threading.Event()

        def _wait():
            while True:
                line = self._proc.stdout.readline()
                if not line or boundary in line:
                    found.set()
                    return

        reader = threading.Thread(target=_wait, daemon=True)
        reader.start()
        reader.join(timeout=timeout)

        if not found.is_set():
            logger.warning("Command timed out after %ds, spawning new shell: %s", timeout, command[:120])
            self._proc = None
            self.start()
            return "", (
                f"Command timed out after {timeout}s but is still running in the old terminal. "
                f"Output is being written to: {out_file}\n"
                "A new terminal has been started for subsequent commands. "
                "You can read the log file above to check progress."
            )

        stdout = ""
        try:
            with open(out_file, "r", encoding="utf-8", errors="replace") as f:
                stdout = f.read()
        except FileNotFoundError:
            pass
        except Exception as e:
            stdout = f"[Error reading command output: {e}]"
        finally:
            try:
                os.remove(out_file)
            except OSError:
                pass

        return stdout, ""

    # -- internals ----------------------------------------------------------

    def _activation_command(self) -> str:
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
