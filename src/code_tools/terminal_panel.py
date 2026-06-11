"""
Terminal panel — ordered, named terminal display with status tracking.

Mirrors ``code_interpreter_panel.py`` and ``tool_store_panel.py``.
Scans message history for ``run_terminal_command`` tool calls, discovers
active terminals, and renders a consolidated display block appended to
tool responses.

Each terminal gets an ordered name (Terminal #1, Terminal #2, …).
Background/timed-out commands show their log file paths so the agent
can ``read_file`` them later.  When a terminal is closed via
``close_terminal_id``, the panel includes the log path in the close
notice so the agent can still find the results.
"""

import json
import os
import re
from typing import Dict, List, Optional, Set

from .panel_manager import Panel

TERMINAL_PANEL_START = "<====TERMINAL_PANEL_START====>"
TERMINAL_PANEL_END   = "<====TERMINAL_PANEL_END====>"

# ── Patterns for extracting log paths from tool responses ────────────
_LOG_PATTERN = re.compile(r"Log:\s*(\S+)")
_OUTFILE_PATTERN = re.compile(r"being written to:\s*(\S+)")

# Limit on output preview length in the panel display
_PREVIEW_LIMIT = 600


# ---------------------------------------------------------------------------
# Discovery — scan message history
# ---------------------------------------------------------------------------

# ── Constants for warnings ──────────────────────────────────────────
_MAX_TERMINALS_WARN = 5       # warn when more terminals are active


def _parse_args(args_raw):
    """Parse tool-call arguments from raw dict or JSON string."""
    if isinstance(args_raw, dict):
        return args_raw
    if isinstance(args_raw, str):
        try:
            return json.loads(args_raw)
        except json.JSONDecodeError:
            return {}
    return {}


def discover_terminals(messages: List[Dict]) -> Dict[str, dict]:
    """
    Scan message history to discover active terminals and their status.

    Returns a dict mapping ``terminal_N`` → terminal info dict:
        id:          str     — e.g. "terminal_1"
        label:       str     — e.g. "Terminal #1"
        command:     str     — the shell command
        blocking:    bool    — whether it was blocking
        status:      str     — "running" | "completed" | "timed_out" | "error"
        log_path:    str|None— path to log file (background / timed-out)
        output_preview: str  — truncated output for completed commands

    Terminals are assigned ordered numeric IDs based on discovery order.
    """
    terminals: Dict[str, dict] = {}
    closed_ids: Set[str] = set()
    terminal_counter = 0
    # Map tool_call_id → terminal_id so we can update info from tool responses
    call_to_terminal: Dict[str, str] = {}
    # Keep track of tool responses we've already processed to avoid duplicates
    seen_responses: Set[str] = set()

    for msg in messages:
        # ── Assistant messages with tool calls ────────────────────────
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if not tc.get("function"):
                    continue
                tool_name = tc["function"].get("name", "")

                # ── close_terminal (separate tool, like close_file) ──
                if tool_name == "close_terminal":
                    args_raw = tc["function"].get("arguments", "{}")
                    args = _parse_args(args_raw)
                    terminal_id = args.get("terminal_id", "")
                    if terminal_id:
                        closed_ids.add(terminal_id)
                    continue

                if tool_name != "run_terminal_command":
                    continue

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

                tc_id = tc.get("id", "")

                # ── Close request ──────────────────────────────────
                close_id = args.get("close_terminal_id")
                if close_id:
                    closed_ids.add(close_id)
                    # Don't create a new terminal for a close request
                    continue

                # ── Refresh-only (no command executed) ─────────────
                refresh = args.get("refresh")
                if refresh:
                    # Just a refresh trigger — no new terminal
                    continue

                # ── Regular command ────────────────────────────────
                command = args.get("command", "")
                if not command:
                    continue

                terminal_counter += 1
                tid = f"terminal_{terminal_counter}"
                label = args.get("terminal_label") or f"Terminal #{terminal_counter}"

                terminals[tid] = {
                    "id": tid,
                    "label": label,
                    "command": command,
                    "blocking": args.get("blocking", True),
                    "status": "running" if not args.get("blocking", True) else "pending",
                    "log_path": None,
                    "output_preview": "",
                }
                if tc_id:
                    call_to_terminal[tc_id] = tid

        # ── Tool response messages ──────────────────────────────────
        if msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id", "")
            if tc_id in seen_responses:
                continue
            seen_responses.add(tc_id)

            content = msg.get("content", "")
            if not content:
                continue

            # Find the terminal this response belongs to
            tid = call_to_terminal.get(tc_id)
            if tid is None or tid not in terminals:
                continue

            term = terminals[tid]

            # ── Extract log path ────────────────────────────────────
            log_match = _LOG_PATTERN.search(content)
            if log_match:
                term["log_path"] = log_match.group(1)

            outfile_match = _OUTFILE_PATTERN.search(content)
            if outfile_match and not term["log_path"]:
                term["log_path"] = outfile_match.group(1)

            # ── Determine status ────────────────────────────────────
            if "timed out" in content.lower() or "timed out after" in content.lower():
                term["status"] = "timed_out"
            elif "error" in content.lower() and "Error:" in content:
                term["status"] = "error"
            elif term["status"] == "running" and term["blocking"] is False:
                # Background command — stays "running" until we see completion
                if "Background PID:" in content:
                    term["status"] = "running"
                elif term["log_path"] and not term["blocking"]:
                    term["status"] = "running"
            elif term["status"] == "pending" and content:
                term["status"] = "completed"

            # ── Extract output preview ──────────────────────────────
            if term["status"] in ("completed", "error", "timed_out"):
                # Try to extract the actual output (skip "Command:" header)
                preview = _extract_preview(content)
                if preview:
                    term["output_preview"] = preview

    # ── Remove closed terminals ──────────────────────────────────────
    # Build a label→id lookup so close_terminal_id can match either form
    label_to_id: Dict[str, str] = {}
    for tid, t in terminals.items():
        label_to_id[t["label"]] = tid
        label_to_id[tid] = tid  # also allow the raw id

    for cid in closed_ids:
        resolved = label_to_id.get(cid, cid)
        terminals.pop(resolved, None)
        # Also remove the terminal from the label lookup so it can't
        # be matched again (belt and suspenders)
        label_to_id.pop(resolved, None)

    return terminals


def _extract_preview(content: str) -> str:
    """Extract a readable preview from a tool response, skipping headers."""
    lines = content.splitlines()
    preview_lines = []
    in_output = False
    for line in lines:
        if line.startswith("STDOUT:") or line.startswith("STDERR:"):
            in_output = True
            continue
        if in_output and line.strip():
            preview_lines.append(line)

    if not preview_lines:
        # Fallback: use the raw content, skip the Command: header
        for line in lines:
            if not line.startswith("Command:") and line.strip():
                preview_lines.append(line)

    preview = "\n".join(preview_lines).strip()
    if len(preview) > _PREVIEW_LIMIT:
        preview = preview[:_PREVIEW_LIMIT] + f"\n\n... [truncated, {len(preview) - _PREVIEW_LIMIT} more chars]"
    return preview


# ---------------------------------------------------------------------------
# Display formatting
# ---------------------------------------------------------------------------

def _render_terminal_entry(term: dict) -> str:
    """Render a single terminal entry for the panel display."""
    status_icon = {
        "running":   "🟢",
        "completed": "✅",
        "timed_out": "⏱️",
        "error":     "❌",
        "pending":   "🟡",
    }.get(term["status"], "❓")

    lines = [
        f"  [{term['label']}] {status_icon} {term['status'].upper()}",
        f"  Command : {term['command'][:200]}",
    ]

    if term.get("log_path"):
        lines.append(f"  Log     : {term['log_path']}")

    if term.get("output_preview"):
        preview = term["output_preview"]
        lines.append(f"  Output  :")
        for pline in preview.splitlines():
            lines.append(f"    │ {pline}")

    if term["status"] == "running":
        lines.append(f"  ⚠️  Background process — use read_file to check: {term.get('log_path', 'N/A')}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Panel implementation
# ---------------------------------------------------------------------------

class TerminalPanel(Panel):
    """Living Tool State panel for terminal sessions."""

    name = "terminals"
    trigger_tools = {"run_terminal_command", "close_terminal"}
    block_start = TERMINAL_PANEL_START
    block_end = TERMINAL_PANEL_END

    def discover(self, messages):
        return discover_terminals(messages)

    def render(self, state):
        if not state:
            return ""

        # Sort by terminal ID to maintain creation order
        sorted_ids = sorted(state.keys(), key=lambda x: int(x.split("_")[1]))
        entries = []
        for tid in sorted_ids:
            entries.append(_render_terminal_entry(state[tid]))

        body = "\n".join(entries)
        header = "── Terminal Sessions ──"

        # ── Warnings ─────────────────────────────────────────────────
        warning = ""
        if len(state) >= _MAX_TERMINALS_WARN:
            warning = (
                f"\n\n⚠️  {len(state)} active terminals — consider closing "
                f"unused ones with the close_terminal tool to keep the display tidy."
            )

        footer = (
            "\n\nTip: Use close_terminal (terminal_id='Terminal #1') to remove "
            "a terminal from this display — the log file path will be returned "
            "so you can read_file it later. "
            "Use run_terminal_command with refresh=true to refresh the panel "
            "without executing a command."
        )

        return f"{self.block_start}\n{header}\n{body}{warning}{footer}\n{self.block_end}"
