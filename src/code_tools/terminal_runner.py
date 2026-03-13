import subprocess
import os
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
import json

# Import session manager
try:
    from ..code_sandbox import session_manager
except ImportError:
    session_manager = None


class TerminalRunner:
    """Terminal command runner with support for background processes."""
    
    def __init__(self, workspace_root: str = None):
        # Use session directory if available, otherwise use provided workspace or current directory
        if session_manager and session_manager.session_dir:
            self.workspace_root = session_manager.get_session_working_directory()
        else:
            self.workspace_root = Path(workspace_root) if workspace_root else Path.cwd()
        
        self.background_processes = {}
        self.process_counter = 0
    
    def run_command(self, command: str, timeout: int = 30, cwd: str = None) -> str:
        """
        Run a terminal command in the persistent session shell.
        
        Args:
            command: The command to execute. If "start_new_terminal", it restarts the shell.
            timeout: Command timeout in seconds (currently not implemented in persistent shell)
            cwd: Working directory (now managed by the session)
            
        Returns:
            Command output or process information
        """
        try:
            if not session_manager:
                return "Error: Session manager is not available."
            
            # Handle restart command
            if command.strip() == "start_new_terminal":
                return session_manager.restart_persistent_shell()

            if not session_manager.persistent_shell:
                return "Error: Persistent shell is not available. Try running 'start_new_terminal'."

            stdout, stderr = session_manager.run_in_persistent_shell(command, timeout=timeout)

            output = []
            output.append(f"Command: {command}")

            if stdout:
                output.append("\nSTDOUT:")
                output.append(stdout)

            if stderr:
                output.append("\nSTDERR:")
                output.append(stderr)

            return '\n'.join(output)
            
        except Exception as e:
            return f"Error running command: {str(e)}"
    
    def _run_foreground_command(self, command: str, work_dir: Path, timeout: int) -> str:
        """This method is no longer the primary way to run commands but kept for compatibility."""
        try:
            use_shell = sys.platform == "win32"

            # This fallback logic remains but should be used less frequently
            if use_shell:
                shell_command = command
            else:
                shell_command = ["bash", "-c", command]

            # Run the command
            result = subprocess.run(
                shell_command,
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=use_shell
            )
            
            # Format output
            output = []
            output.append(f"Command: {command}")
            output.append(f"Working Directory: {work_dir}")
            output.append(f"Exit Code: {result.returncode}")
            output.append("")
            
            if result.stdout:
                output.append("STDOUT:")
                output.append(result.stdout)
                output.append("")
            
            if result.stderr:
                output.append("STDERR:")
                output.append(result.stderr)
                output.append("")
            
            if result.returncode != 0:
                output.append(f"Command failed with exit code {result.returncode}")
            
            return '\n'.join(output)
            
        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout} seconds: {command}"
        except Exception as e:
            return f"Error executing command: {str(e)}"


def run_terminal_cmd_tool(command: str, workspace_root: str = None) -> str:
    """Run terminal command tool wrapper."""
    runner = TerminalRunner(workspace_root=workspace_root)
    return runner.run_command(command)

# To maintain compatibility if other parts of the codebase use these
def list_background_processes_tool(workspace_root: str = None) -> str:
    return "Background process functionality has been removed."

def stop_background_process_tool(process_id: str, workspace_root: str = None) -> str:
    return "Background process functionality has been removed."

def get_process_output_tool(process_id: str, workspace_root: str = None) -> str:
    return "Background process functionality has been removed." 