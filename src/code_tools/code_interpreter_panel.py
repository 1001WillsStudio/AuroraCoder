"""
Code interpreter panel — consolidated, line-numbered file display.

Scans message history for file-touching tool calls (read/write/edit),
discovers which files the agent is working with, and renders a
consolidated display block appended to tool responses.

Mirrors ``tool_store_panel.py``.  State lives in message history — no
separate session objects.
"""

import json
from typing import Dict, List, Set

from .panel_manager import Panel
from ..code_sandbox import WORKSPACE
from ..config import CONTEXT_DISPLAY_WARN_CHARS, CONTEXT_DISPLAY_MAX_ITEMS

CODE_INTERPRETER_START = "<====CODE_INTERPRETER_START====>"
CODE_INTERPRETER_END   = "<====CODE_INTERPRETER_END====>"

# Tools that add a file to the display
CODE_RELATED_TOOLS = {'read_file', 'write_file', 'edit_file'}

# Tools that remove a file from the display
FILE_REMOVAL_TOOLS = {'delete_file', 'close_file'}


# ---------------------------------------------------------------------------
# Discovery — scan message history
# ---------------------------------------------------------------------------

def discover_open_files(messages: List[Dict]) -> Set[str]:
    """
    Scan message history to discover all files that should be displayed.

    Files are added when read_file, write_file, or edit_file is called,
    and removed when delete_file or close_file is called (order matters).

    Args:
        messages: List of message dictionaries

    Returns:
        Set of file paths that should be displayed
    """
    open_files: Set[str] = set()

    for msg in messages:
        # Look for assistant messages with tool calls
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if not tc.get("function"):
                    continue

                tool_name = tc["function"].get("name", "")
                args_raw = tc["function"].get("arguments", "{}")

                # Normalise: arguments may already be a dict or a JSON string
                if isinstance(args_raw, dict):
                    args = args_raw
                elif isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw)
                    except json.JSONDecodeError:
                        continue
                else:
                    continue

                # After normalisation args may still be a plain string
                # (e.g. when the JSON payload is a bare string literal)
                if not isinstance(args, dict):
                    continue

                target = args.get("file")

                # ── Handle keep on close_file (close all except) ────────
                if tool_name == "close_file" and "keep" in args:
                    keep_list = args["keep"]
                    if isinstance(keep_list, list):
                        if keep_list:
                            open_files.intersection_update(keep_list)
                        else:
                            open_files.clear()
                    elif keep_list:
                        # Single string, not in a list
                        open_files.intersection_update({keep_list})
                    else:
                        open_files.clear()
                    continue


                if not target:
                    continue

                # ── Normalise target to list ──────────────────────────
                if isinstance(target, list):
                    targets = target
                else:
                    targets = [target]

                if tool_name in CODE_RELATED_TOOLS:
                    for t in targets:
                        open_files.add(t)
                elif tool_name in FILE_REMOVAL_TOOLS:
                    for t in targets:
                        open_files.discard(t)

    return open_files


# ---------------------------------------------------------------------------
# Display formatting
# ---------------------------------------------------------------------------

def _format_code(code: str) -> str:
    """Format code with right-aligned line numbers."""
    lines = code.splitlines()
    width = len(str(len(lines)))
    return "\n".join(
        f"{str(i + 1).rjust(width)}|{line}"
        for i, line in enumerate(lines)
    )


def _display_file(filepath: str) -> str:
    """Read a single file (relative to WORKSPACE) with line numbers."""
    try:
        full_path = WORKSPACE / filepath
        if not full_path.is_file():
            return f"--- {filepath} ---\n[File not found: {filepath}]"
        with open(full_path, 'r', encoding='utf-8') as f:
            code = f.read()
        return f"--- {filepath} ---\n{_format_code(code)}"
    except Exception as e:
        return f"--- {filepath} ---\n[Error reading file: {str(e)}]"


# ---------------------------------------------------------------------------
# Panel implementation
# ---------------------------------------------------------------------------

class CodeInterpreterPanel(Panel):
    """Living Tool State panel for workspace files."""

    name = "files"
    trigger_tools = CODE_RELATED_TOOLS | FILE_REMOVAL_TOOLS
    block_start = CODE_INTERPRETER_START
    block_end = CODE_INTERPRETER_END

    def discover(self, messages):
        return discover_open_files(messages)

    def render(self, state):
        if not state:
            return ""

        sorted_files = sorted(state)
        combined = "\n\n".join(_display_file(f) for f in sorted_files)

        notes = (
            "\n\nNote: This display shows the LATEST state of each file "
            "with accurate line numbers. Always use these line numbers "
            "for edit_file calls — never use memorised line numbers. "
            "Closing a file removes it from this display, including "
            "previous tool responses — you will no longer see its "
            "contents unless you open it again. Only close a file "
            "after you have fully extracted all information you need from it."
        )
        if len(state) > CONTEXT_DISPLAY_MAX_ITEMS or len(combined) > CONTEXT_DISPLAY_WARN_CHARS:
            notes += (
                f"\n⚠️ CONTEXT WARNING: You have {len(state)} files open "
                f"({', '.join(sorted_files)}). "
                "To avoid running out of context, close files you no longer "
                "need with close_file(file='file.py'), close multiple "
                "at once with close_file(file=['a.py','b.py']), or "
                "keep only what you need with close_file(keep=['kept.py'])."
            )
        return f"{self.block_start}\n{combined}{notes}\n{self.block_end}"
