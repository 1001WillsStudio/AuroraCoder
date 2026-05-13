import ast
import tempfile
import os
import json
import sys
from io import StringIO
from pathlib import Path
from typing import Optional, List, Set
import subprocess
import logging

from ..code_sandbox import WORKSPACE, get_python_path, get_conda_env_path
from ..config import CODE_INTERPRETER_ERRORS_ENABLED

logger = logging.getLogger(__name__)

# Constants for code interpreter markers
CODE_INTERPRETER_START = "<====CODE_INTERPRETER_START====>"
CODE_INTERPRETER_END = "<====CODE_INTERPRETER_END====>"


class CodeInterpreter:
    """
    A class to manage code analysis and display for multiple files.
    Supports displaying multiple files in a consolidated view.
    Uses pyright for type checking with the session's conda environment.
    """

    def __init__(self):
        self.root_path: Optional[Path] = None
        self._cached_python_path: Optional[Path] = None
        self._cached_venv_path: Optional[Path] = None

    def set_root_path(self, path: Path):
        """Sets the root directory for analysis."""
        self.root_path = path

    def display_single_file(self, filepath: str) -> str:
        """
        Analyzes a single file and formats its content with errors.
        Returns the formatted content without the interpreter markers.
        """
        if not self.root_path:
            return f"Error: Root path has not been set."

        try:
            full_path = self.root_path / filepath

            if not full_path.is_file():
                return f"[File not found: {filepath}]"

            with open(full_path, 'r', encoding='utf-8') as f:
                code = f.read()

            errors = self._check_code(code, filename=filepath)
            formatted_code = self._format_code(code, errors)
            
            return f"--- {filepath} ---\n{formatted_code}"

        except Exception as e:
            return f"--- {filepath} ---\n[Error reading file: {str(e)}]"

    def display_multiple_files(self, filepaths: List[str]) -> str:
        """
        Displays multiple files in a consolidated code interpreter block.
        
        Args:
            filepaths: List of relative file paths to display
            
        Returns:
            Consolidated code interpreter block with all files
        """
        if not filepaths:
            return ""
        
        if not self.root_path:
            self.root_path = WORKSPACE
        
        file_sections = []
        for filepath in filepaths:
            section = self.display_single_file(filepath)
            file_sections.append(section)
        
        combined_content = "\n\n".join(file_sections)
        return f"{CODE_INTERPRETER_START}\n{combined_content}\n{CODE_INTERPRETER_END}"

    def _format_code(self, code: str, errors: dict = None) -> str:
        """
        Formats a string of code with line numbers and optional errors.
        (Internal method)
        """
        if errors is None:
            errors = {}

        lines = code.split('\n')
        max_line_num = len(lines)
        line_num_width = len(str(max_line_num))

        formatted_lines = []
        for i, line in enumerate(lines):
            line_num = i + 1
            line_num_str = str(line_num).rjust(line_num_width)
            formatted_lines.append(f"{line_num_str}|{line}")
            if line_num in errors:
                error_message = errors[line_num]
                error_indent = ' ' * (line_num_width + 1)  # width + '|'
                error_lines = error_message.split('\n')
                for error_line in error_lines:
                    formatted_lines.append(f"{error_indent}{error_line}")

        formatted_output = "\n".join(formatted_lines)
        return formatted_output

    def _get_session_python_path(self) -> Optional[Path]:
        """Get the Python path from the session's conda environment (cached)."""
        if self._cached_python_path is not None:
            return self._cached_python_path
        
        python_path = get_python_path()
        if python_path:
            self._cached_python_path = python_path
            logger.debug(f"Using Python path for pyright: {python_path}")
        return python_path

    def _get_session_venv_path(self) -> Optional[Path]:
        """Get the conda environment path (cached)."""
        if self._cached_venv_path is not None:
            return self._cached_venv_path
        
        venv_path = get_conda_env_path()
        if venv_path:
            self._cached_venv_path = venv_path
            logger.debug(f"Using venv path for pyright: {venv_path}")
        return venv_path

    def clear_python_path_cache(self):
        """Clear the cached Python path (call after session changes)."""
        self._cached_python_path = None
        self._cached_venv_path = None

    def _create_pyright_config(self, temp_dir: str) -> Optional[str]:
        """Create a pyrightconfig.json for the session's conda environment.
        
        Args:
            temp_dir: Directory where the temp file will be created
            
        Returns:
            Path to the created pyrightconfig.json, or None if not needed
        """
        python_path = self._get_session_python_path()
        venv_path = self._get_session_venv_path()
        
        if not python_path and not venv_path:
            return None
        
        config = {
            "typeCheckingMode": "basic",
            "reportMissingImports": False,  # Module might be installed at runtime
            "reportMissingTypeStubs": False,
            "reportMissingModuleSource": False,
        }
        
        # Set pythonPath to the conda environment's Python
        if python_path:
            config["pythonPath"] = str(python_path)
        
        # Set venvPath and venv for proper environment resolution
        if venv_path:
            # venvPath is the parent directory containing the venv
            config["venvPath"] = str(venv_path.parent)
            config["venv"] = venv_path.name
        
        # Add the session's working directory to extraPaths if available
        if self.root_path:
            config["extraPaths"] = [str(self.root_path)]
        
        config_path = os.path.join(temp_dir, "pyrightconfig.json")
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
        
        return config_path

    def _check_code(self, code: str, filename: str = "temp.py") -> dict:
        """
        Checks code for errors using Pyright for .py files.
        Uses the session's conda environment for type checking.
        No checks are performed for other file types. (Internal method)
        Disabled entirely when CODE_INTERPRETER_ERRORS_ENABLED is False.
        """
        if not CODE_INTERPRETER_ERRORS_ENABLED:
            return {}
        if filename.endswith(".py"):
            temp_dir = None
            try:
                # Create a temporary directory to hold both the file and pyrightconfig
                temp_dir = tempfile.mkdtemp(prefix="pyright_check_")
                temp_filepath = os.path.join(temp_dir, os.path.basename(filename))
                
                with open(temp_filepath, 'w', encoding='utf-8') as temp_file:
                    temp_file.write(code)

                # Create pyrightconfig.json for the session's conda environment
                self._create_pyright_config(temp_dir)

                # Run Pyright and capture JSON diagnostics
                try:
                    result = subprocess.run(
                        ["pyright", "--outputjson", temp_filepath],
                        capture_output=True,
                        text=True,
                        check=False,  # Pyright uses non-zero exit codes to signal diagnostics
                        cwd=temp_dir  # Run from the temp dir so pyright finds the config
                    )
                except FileNotFoundError:
                    # Pyright is not installed; skip analysis
                    return {}

                try:
                    report = json.loads(result.stdout)
                except (json.JSONDecodeError, TypeError):
                    # If Pyright emitted non-JSON output, ignore
                    return {}

                errors = {}
                for diag in report.get("generalDiagnostics", []):
                    # Convert 0-based → 1-based line numbers
                    line_num = diag.get("range", {}).get("start", {}).get("line", 0) + 1
                    message = diag.get("message", "Unknown error")
                    if line_num in errors:
                        errors[line_num] += f"\n{message}"
                    else:
                        errors[line_num] = message
                return errors
            finally:
                # Clean up the temporary directory
                if temp_dir and os.path.exists(temp_dir):
                    import shutil
                    shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            return {}


# Global instance of the Code Interpreter
code_interpreter = CodeInterpreter()
