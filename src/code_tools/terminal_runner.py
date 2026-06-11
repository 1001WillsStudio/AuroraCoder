import subprocess
import os
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import json

from ..code_sandbox import shell, WORKSPACE
from ..config import TERMINAL_MAX_OUTPUT_CHARS


def _get_terminal_max_output_chars() -> int:
    """Resolve terminal-max-output-chars: env var (set by _sync_tool_env_vars) → config constant."""
    try:
        val = int(os.environ.get("TERMINAL_MAX_OUTPUT_CHARS", ""))
        if val > 0:
            return val
    except (ValueError, TypeError):
        pass
    return TERMINAL_MAX_OUTPUT_CHARS


def _truncate_output(text: str, limit: int | None = None) -> str:
    """Keep the first and last portions of long output, dropping the middle."""
    if limit is None:
        limit = _get_terminal_max_output_chars()
    if len(text) <= limit:
        return text
    keep = limit // 2
    head = text[:keep]
    tail = text[-keep:]
    dropped = len(text) - keep * 2
    return f"{head}\n\n... [{dropped:,} characters truncated] ...\n\n{tail}"


class TerminalRunner:
    """Terminal command runner with support for background processes."""
    
    def __init__(self, workspace_root: str = None):
        self.workspace_root = WORKSPACE
    
    def run_command(
        self, command: str, timeout: int = 30, blocking: bool = True, cwd: str = None, new_terminal: bool = False
    ) -> str:
        """
        Run a terminal command in the persistent session shell.

        Args:
            command: The command to execute.
            timeout: Command timeout in seconds.
            blocking: If False, launch in background and return the log file path.
            cwd: Working directory (now managed by the session).
            new_terminal: If True, restart the persistent shell before running the command.

        Returns:
            Command output or process information
        """
        try:

            if new_terminal:
                shell.restart()

            if not shell.is_alive:
                return "Error: Persistent shell is not available. Use new_terminal=true to restart it."

            stdout, stderr = shell.run(command, timeout=timeout, blocking=blocking)

            output = []

            if stdout:
                output.append("\nSTDOUT:")
                output.append(_truncate_output(stdout))

            if stderr:
                output.append("\nSTDERR:")
                output.append(_truncate_output(stderr))

            if not stdout and not stderr:
                output.append(
                    "\n[NOTE: Command produced no output. "
                    "If empty-output issues persist, try calling this tool again "
                    "with new_terminal=true to restart the shell.]"
                )

            return '\n'.join(output)

        except Exception as e:
            return f"Error running command: {str(e)}"
    
    def _run_foreground_command(self, command: str, work_dir: Path, timeout: int) -> str:
        """This method is no longer the primary way to run commands but kept for compatibility."""
        try:
            use_shell = sys.platform == "win32"

            if use_shell:
                shell_command = command
            else:
                shell_command = ["bash", "-c", command]

            result = subprocess.run(
                shell_command,
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=use_shell
            )
            
            output = []
            output.append(f"Working Directory: {work_dir}")
            output.append(f"Exit Code: {result.returncode}")
            output.append("")
            
            if result.stdout:
                output.append("STDOUT:")
                output.append(_truncate_output(result.stdout))
                output.append("")
            
            if result.stderr:
                output.append("STDERR:")
                output.append(_truncate_output(result.stderr))
                output.append("")
            
            if result.returncode != 0:
                output.append(f"Command failed with exit code {result.returncode}")
            
            return '\n'.join(output)
            
        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout} seconds: {command}"
        except Exception as e:
            return f"Error executing command: {str(e)}"


def run_terminal_cmd_tool(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Run terminal command tool wrapper."""
    runner = TerminalRunner(workspace_root=arguments.get("workspace_root"))
    return runner.run_command(
        command=arguments["command"],
        timeout=arguments.get("timeout", 30),
        blocking=arguments.get("blocking", True),
        new_terminal=arguments.get("new_terminal", False),
    ), arguments
