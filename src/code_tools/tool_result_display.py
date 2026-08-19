"""Sanitize tool-result text for the chat UI.

Living-tool-state panels (code interpreter, ToolStore, memory) append
protocol-delimited dumps to the *LLM* tool message. Those markers, the
edit_file line-number notes, and raw Python exceptions must not appear
in the tool-activity card.
"""

from __future__ import annotations

import re

PANEL_START_MARKERS = (
    "<====CODE_INTERPRETER_START====>",
    "<====TOOLSTORE_START====>",
    "<====MEMORY_START====>",
)

_FILE_NOT_FOUND_RE = re.compile(r"\[File not found: ([^\]]+)\]")
_EXEC_TOOL_RE = re.compile(r"^Error executing tool '([^']+)': (.*)", re.DOTALL)
_PY_INTERNAL_SNIPPETS = ("cannot unpack", "nonetype", "traceback")


def strip_panel_markup(content: str) -> str:
    """Drop living-tool-state blocks, keeping the tool's own result text."""
    if not content:
        return ""
    cut = None
    for marker in PANEL_START_MARKERS:
        idx = content.find(marker)
        if idx != -1 and (cut is None or idx < cut):
            cut = idx
    if cut is None:
        return content
    return content[:cut].rstrip()


def _looks_like_python_exception(detail: str) -> bool:
    lower = (detail or "").lower()
    return any(snippet in lower for snippet in _PY_INTERNAL_SNIPPETS)


def sanitize_tool_content_for_ui(content: str) -> str:
    """User-facing tool output: no protocol wrappers, no Python internals."""
    raw = content or ""
    not_found = _FILE_NOT_FOUND_RE.search(raw)
    text = strip_panel_markup(raw)

    exec_match = _EXEC_TOOL_RE.match(text.lstrip()) if text else None
    if exec_match and _looks_like_python_exception(exec_match.group(2)):
        if not_found:
            return f"File not found: {not_found.group(1)}"
        return f"Error: {exec_match.group(1)} failed"

    if not text and not_found:
        return f"File not found: {not_found.group(1)}"
    return text


def tool_result_is_error(content: str) -> bool:
    """True when the (already sanitized) tool output is a failure."""
    text = (content or "").lstrip()
    if not text:
        return False
    if text.startswith("Error"):
        return True
    if text.lower().startswith("file not found"):
        return True
    if "[File not found:" in text:
        return True
    return False
