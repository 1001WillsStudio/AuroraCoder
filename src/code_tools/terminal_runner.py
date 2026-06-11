import subprocess
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import json

from ..code_sandbox import shell, WORKSPACE
from ..config import TERMINAL_MAX_OUTPUT_CHARS

# ── Module-level terminal registry ──────────────────────────────────
# Tracks log-file paths so close_terminal_id can return them.
_terminal_log_cache: Dict[str, str] = {}  # label → log_path
_terminal_counter: int = 0

_LOG_PATH_RE = re.compile(r"Log:\s*(\S+)")
_OUTFILE_RE = re.compile(r"being written to:\s*(\S+)")


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
        self,
        command: str = "",
        timeout: int = 30,
        blocking: bool = True,
        cwd: str = None,
        new_terminal: bool = False,
        close_terminal_id: str = None,
        refresh: bool = False,
        terminal_label: str = None,
    ) -> str:
        """
        Run a terminal command in the persistent session shell.

        Args:
            command: The command to execute.
            timeout: Command timeout in seconds.
            blocking: If False, launch in background and return the log file path.
            cwd: Working directory (now managed by the session).
            new_terminal: If True, restart the persistent shell before running the command.
            close_terminal_id: If set, close this terminal from the display without
                executing a command.  The log file path (if any) is returned so the
                agent can ``read_file`` it later.
            refresh: If True, just refresh the terminal panel without executing anything.
            terminal_label: Optional human-readable label for this terminal.

        Returns:
            Command output or process information
        """
        try:
            # ── Close terminal ────────────────────────────────────
            if close_terminal_id:
                return (
                    f"Closed terminal \"{close_terminal_id}\".\n"
                    f"The log file path for this terminal (if any) is shown in the terminal panel. "
                    f"Use read_file to access the complete output."
                )

            # ── Refresh-only (no command executed) ────────────────
            if refresh:
                return "Terminal panel refreshed. No command executed."

            if new_terminal:
                shell.restart()

            if not shell.is_alive:
                return "Error: Persistent shell is not available. Use new_terminal=true to restart it."

            stdout, stderr = shell.run(command, timeout=timeout, blocking=blocking)

            output = []
            output.append(f"Command: {command}")

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
            output.append(f"Command: {command}")
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
    """Run terminal command tool wrapper with log-path tracking."""
    global _terminal_counter, _terminal_log_cache

    # ── Close terminal — look up and return the log path ────────────
    close_id = arguments.get("close_terminal_id")
    if close_id:
        log_path = _terminal_log_cache.pop(close_id, None)
        if log_path:
            return (
                f"Closed terminal \"{close_id}\".\n"
                f"Log file: {log_path}\n"
                f"Use read_file to access the complete output."
            ), arguments
        else:
            return (
                f"Closed terminal \"{close_id}\". "
                f"No log file was associated with this terminal."
            ), arguments

    # ── Refresh-only ────────────────────────────────────────────────
    if arguments.get("refresh"):
        return "Terminal panel refreshed. No command executed.", arguments

    # ── Auto-generate terminal label ────────────────────────────────
    terminal_label = arguments.get("terminal_label")
    _terminal_counter += 1
    if not terminal_label:
        terminal_label = f"Terminal #{_terminal_counter}"
    tid = f"terminal_{_terminal_counter}"

    runner = TerminalRunner(workspace_root=arguments.get("workspace_root"))
    result = runner.run_command(
        command=arguments.get("command", ""),
        timeout=arguments.get("timeout", 30),
        blocking=arguments.get("blocking", True),
        new_terminal=arguments.get("new_terminal", False),
        close_terminal_id=None,   # already handled above
        refresh=False,            # already handled above
        terminal_label=terminal_label,
    )

    # ── Extract and cache log path ──────────────────────────────────
    log_match = _LOG_PATH_RE.search(result)
    if log_match:
        log_path = log_match.group(1)
        _terminal_log_cache[terminal_label] = log_path
        _terminal_log_cache[tid] = log_path
    else:
        outfile_match = _OUTFILE_RE.search(result)
        if outfile_match:
            log_path = outfile_match.group(1)
            _terminal_log_cache[terminal_label] = log_path
            _terminal_log_cache[tid] = log_path

    return result, arguments


# ---------------------------------------------------------------------------
# close_terminal — separate tool, mirrors close_file for terminals
# ---------------------------------------------------------------------------

def close_terminal_tool(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Close (remove) a terminal from the Terminal Panel display.

    Takes a ``terminal_id`` matching the label shown in the panel
    (e.g. "Terminal #1" or a custom ``terminal_label``).  The terminal's
    log file path is returned so you can still ``read_file`` it later.

    No command is executed — this only manages the panel display.

    Returns:
        (result_str, applied_arguments)
    """
    terminal_id = arguments.get("terminal_id", "")
    if not terminal_id:
        return "Error: terminal_id is required to close a terminal.", arguments

    log_path = _terminal_log_cache.pop(terminal_id, None)
    if log_path:
        return (
            f"Closed terminal \"{terminal_id}\".\n"
            f"Log file: {log_path}\n"
            f"Use read_file to access the complete output."
        ), arguments
    else:
        return (
            f"Closed terminal \"{terminal_id}\". "
            f"No log file was associated with this terminal."
        ), arguments
