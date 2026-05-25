"""Thin subprocess wrapper around GNU grep — fast, real, no Python reimplementation."""

import subprocess
from pathlib import Path

from ..code_sandbox import WORKSPACE


def _build_grep_cmd(query: str, include_pattern: str | None,
                    exclude_pattern: str | None, case_sensitive: bool) -> list[str]:
    """Build the grep argument list."""
    cmd = ["grep", "-rnI", "--color=never"]

    if not case_sensitive:
        cmd.append("-i")

    if include_pattern:
        cmd.extend(["--include", include_pattern])
    if exclude_pattern:
        cmd.extend(["--exclude", exclude_pattern])

    cmd.extend([query, str(WORKSPACE)])
    return cmd


def grep_search_tool(query: str, include_pattern: str = None,
                     exclude_pattern: str = None, case_sensitive: bool = True,
                     max_lines: int = 200) -> str:
    """Run real `grep` as a subprocess and return the output.

    Args:
        query:           Regex pattern to search for (as passed to grep -E by default).
        include_pattern: Glob for files to include (e.g. ``*.py``).
        exclude_pattern: Glob for files to exclude.
        case_sensitive:  If False, ``grep -i`` is added.
        max_lines:       Trim output to this many lines.
    """

    cmd = _build_grep_cmd(query, include_pattern, exclude_pattern, case_sensitive)

    try:
        cp = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(WORKSPACE),
        )
    except subprocess.TimeoutExpired:
        return f"Error: grep timed out after 15 s searching for pattern: {query}"
    except FileNotFoundError:
        return "Error: grep binary not found on this system."

    out = cp.stdout.strip()

    # Truncate if needed
    lines = out.split("\n")
    if len(lines) > max_lines:
        out = "\n".join(lines[:max_lines])
        out += f"\n... (truncated to {max_lines} lines; {len(lines)} total matches)"

    if not out:
        if cp.returncode == 0:
            return f"No matches found for pattern: {query}"
        # grep returns 1 for "no matches" and 2 for errors
        return f"No matches found for pattern: {query}"

    return f"Search results for pattern: {query}\n\n{out}"
