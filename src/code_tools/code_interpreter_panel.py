"""
Code interpreter panel — consolidated, line-numbered file display.

Scans message history for file-touching tool calls (manage_open_files,
write, edit, delete), discovers which files the agent is working with,
and renders a consolidated display block appended to tool responses.

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
CODE_RELATED_TOOLS = {'write_file', 'edit_file'}

# Tools that remove a file from the display
FILE_REMOVAL_TOOLS = {'delete_file'}


# ---------------------------------------------------------------------------
# Discovery — scan message history
# ---------------------------------------------------------------------------

def discover_open_files(messages: List[Dict]) -> Set[str]:
    """
    Scan message history to discover all files that should be displayed.

    ``manage_open_files`` replaces the entire set; ``write_file`` /
    ``edit_file`` auto-add; ``delete_file`` auto-removes.
    Order of tool calls in history matters — later calls override earlier ones.
    """
    open_files: Set[str] = set()

    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if not tc.get("function"):
                    continue

                tool_name = tc["function"].get("name", "")
                args_raw = tc["function"].get("arguments", "{}")

                if isinstance(args_raw, dict):
                    args = args_raw
                elif isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw)
                    except json.JSONDecodeError:
                        continue
                else:
                    continue

                if not isinstance(args, dict):
                    continue

                # ── ``manage_open_files`` — full set or additive ────
                if tool_name == "manage_open_files":
                    files = args.get("files", [])
                    if isinstance(files, str):
                        files = [files]
                    if not isinstance(files, list):
                        files = []
                    new_set = set(f for f in files if isinstance(f, str))
                    if args.get("additive"):
                        open_files |= new_set
                    else:
                        open_files = new_set
                    continue

                # ── add / remove individual files ───────────────────
                target_file = args.get("target_file")
                if not target_file:
                    continue

                if tool_name in CODE_RELATED_TOOLS:
                    open_files.add(target_file)
                elif tool_name in FILE_REMOVAL_TOOLS:
                    open_files.discard(target_file)

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
            return f"--- {filepath}  [not found] ---"
        with open(full_path, 'r', encoding='utf-8') as f:
            code = f.read()
        lc = code.count('\n') + (1 if code and not code.endswith('\n') else 0)
        return f"--- {filepath}  ({lc} lines) ---\n{_format_code(code)}"
    except Exception as e:
        return f"--- {filepath}  [error] ---\n[Error reading file: {str(e)}]"


# ---------------------------------------------------------------------------
# Panel implementation
# ---------------------------------------------------------------------------

class CodeInterpreterPanel(Panel):
    """Living Tool State panel for workspace files."""

    name = "files"
    trigger_tools = CODE_RELATED_TOOLS | FILE_REMOVAL_TOOLS | {'manage_open_files'}
    block_start = CODE_INTERPRETER_START
    block_end = CODE_INTERPRETER_END

    def discover(self, messages):
        return discover_open_files(messages)

    def render(self, state):
        if not state:
            return ""

        # ── gather metadata in one pass ──────────────────────────────
        file_info = []  # (path, line_count, error)
        for f in state:
            p = WORKSPACE / f
            if not p.is_file():
                file_info.append((f, 0, "[not found]"))
                continue
            try:
                content = p.read_text(encoding='utf-8')
                lc = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
                file_info.append((f, lc, None))
            except Exception:
                file_info.append((f, 0, "[error]"))

        # sort largest first so the agent spots context hogs
        sorted_info = sorted(file_info, key=lambda x: x[1], reverse=True)
        sorted_files = [fi[0] for fi in sorted_info]
        combined = "\n\n".join(_display_file(f) for f in sorted_files)

        # ── summary header with per-file line counts ─────────────────
        total_lines = sum(fi[1] for fi in sorted_info)
        summary_lines = []
        for path, lc, err in sorted_info:
            if err:
                summary_lines.append(f"    {path:40s}  {err}")
            else:
                summary_lines.append(f"    {path:40s} {lc:>5}L")

        header = (
            f"\U0001f4c2 Open files ({len(state)}) — {total_lines} lines total\n"
            + "\n".join(summary_lines)
        )

        # ── notes ───────────────────────────────────────────────────
        notes = (
            "\n\nNote: This display shows the LATEST state of each file "
            "with accurate line numbers. Always use these line numbers "
            "for edit_file calls — never use memorised line numbers. "
            "Use manage_open_files() to set which files are visible — "
            "files not listed are removed from this display."
        )
        if len(state) > CONTEXT_DISPLAY_MAX_ITEMS or len(combined) > CONTEXT_DISPLAY_WARN_CHARS:
            largest = [fi[0] for fi in sorted_info[:3]]
            notes += (
                f"\n\u26a0\ufe0f CONTEXT WARNING: {len(state)} files open "
                f"({total_lines:,} lines).  "
                f"Use manage_open_files() to keep only what you need.  "
                f"Largest files: "
                + ", ".join(largest)
                + "."
            )
        return f"{self.block_start}\n{header}{notes}\n\n{combined}\n{self.block_end}"
