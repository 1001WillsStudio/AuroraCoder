"""Code Interpreter — displays file contents to the agent in a consolidated view."""

from pathlib import Path
from typing import Optional, List

from ..code_sandbox import WORKSPACE

# Constants for code interpreter markers
CODE_INTERPRETER_START = "<====CODE_INTERPRETER_START====>"
CODE_INTERPRETER_END   = "<====CODE_INTERPRETER_END====>"


class CodeInterpreter:
    """Manages file content display for the agent."""

    def __init__(self):
        self.root_path: Optional[Path] = None

    def set_root_path(self, path: Path):
        self.root_path = path

    def display_single_file(self, filepath: str) -> str:
        """Read a single file and return it with line numbers."""
        if not self.root_path:
            return "Error: Root path has not been set."

        try:
            full_path = self.root_path / filepath
            if not full_path.is_file():
                return f"[File not found: {filepath}]"

            with open(full_path, 'r', encoding='utf-8') as f:
                code = f.read()

            return f"--- {filepath} ---\n{self._format_code(code)}"

        except Exception as e:
            return f"--- {filepath} ---\n[Error reading file: {str(e)}]"

    def display_multiple_files(self, filepaths: List[str]) -> str:
        """Display multiple files in a consolidated block."""
        if not filepaths:
            return ""

        if not self.root_path:
            self.root_path = WORKSPACE

        file_sections = []
        for filepath in filepaths:
            file_sections.append(self.display_single_file(filepath))

        combined = "\n\n".join(file_sections)
        return f"{CODE_INTERPRETER_START}\n{combined}\n{CODE_INTERPRETER_END}"

    def _format_code(self, code: str) -> str:
        """Format code with line numbers."""
        lines = code.splitlines()
        width = len(str(len(lines)))

        formatted_lines = []
        for i, line in enumerate(lines):
            line_num_str = str(i + 1).rjust(width)
            formatted_lines.append(f"{line_num_str}|{line}")

        return "\n".join(formatted_lines)


# Global instance
code_interpreter = CodeInterpreter()
