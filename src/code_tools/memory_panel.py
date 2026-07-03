"""
Memory panel — "Living Tool State" display for the ``remember``/``recall``
tools (see ``panel_manager.py`` for the Panel pattern this follows,
mirroring ``tool_store_panel.py``).

Gives the user visibility into what the agent chose to persist to
long-term memory during this session — the same transparency the
ToolStore panel gives for open toolsets.
"""

from __future__ import annotations

import json
from typing import Dict, List

from .panel_manager import Panel

MEMORY_START = "<====MEMORY_START====>"
MEMORY_END = "<====MEMORY_END====>"


def _discover_remembered(messages: List[Dict]) -> List[Dict]:
    """Scan history for successful ``remember`` calls (paired with their
    tool result so we only show ones that actually succeeded)."""
    remembered: List[Dict] = []

    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function")
            if not fn or fn.get("name") != "remember":
                continue
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue

            result_text = _find_tool_result(messages, tc.get("id"))
            if not result_text or result_text.startswith("Failed to write memory"):
                continue

            remembered.append({
                "description": args.get("description", ""),
                "type": args.get("type", "project"),
                "plane": args.get("plane", "world"),
            })

    return remembered


def _discover_forgotten(messages: List[Dict]) -> List[str]:
    """Scan history for successful ``forget`` calls (paired with their tool
    result so we only show ones that actually deleted something)."""
    forgotten: List[str] = []

    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function")
            if not fn or fn.get("name") != "forget":
                continue
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue

            result_text = _find_tool_result(messages, tc.get("id"))
            if not result_text.startswith("Forgot memory"):
                continue

            forgotten.append(args.get("memory_id", "?"))

    return forgotten


def _find_tool_result(messages: List[Dict], tool_call_id: str) -> str:
    for msg in messages:
        if msg.get("role") == "tool" and msg.get("tool_call_id") == tool_call_id:
            return msg.get("content", "")
    return ""


class MemoryPanel(Panel):
    """Living Tool State panel for the memory system's ``remember``/``forget`` tools."""

    name = "memory"
    trigger_tools = {"remember", "recall", "forget"}
    block_start = MEMORY_START
    block_end = MEMORY_END

    def discover(self, messages):
        return {
            "remembered": _discover_remembered(messages),
            "forgotten": _discover_forgotten(messages),
        }

    def render(self, state):
        remembered = state.get("remembered", [])
        forgotten = state.get("forgotten", [])
        if not remembered and not forgotten:
            return ""

        lines = []
        if remembered:
            lines.append(f"🧠 Remembered {len(remembered)} fact{'s' if len(remembered) != 1 else ''} this session:")
            for item in remembered:
                lines.append(f"- [{item['plane']}/{item['type']}] {item['description']}")
        if forgotten:
            if lines:
                lines.append("")
            lines.append(f"🗑️ Forgot {len(forgotten)} memor{'y' if len(forgotten) == 1 else 'ies'} this session:")
            for memory_id in forgotten:
                lines.append(f"- {memory_id}")

        return f"{self.block_start}\n" + "\n".join(lines) + f"\n{self.block_end}"
