"""
Tool Store panel — code-interpreter pattern for ToolStore tools.

Mirrors ``code_interpreter_panel.py``.  Scans message history for ``tool_store``
calls, discovers which tools / MCP servers / skills the agent has
referenced, and generates a consolidated display block appended to
tool responses.

State lives in message history — no separate session objects.
"""

from __future__ import annotations

import json
from typing import Dict, List, Set

from .panel_manager import Panel

try:
    from toolstore.native_tool import tool_store_tool as _raw_ts
except ImportError:
    _raw_ts = None

try:
    from toolstore.tool import Tool
except ImportError:
    Tool = None
from ..config import CONTEXT_DISPLAY_WARN_CHARS, CONTEXT_DISPLAY_MAX_ITEMS
from ..core_tools.tool_store_client import tool_store_tool

TOOLSTORE_START = "<====TOOLSTORE_START====>"
TOOLSTORE_END   = "<====TOOLSTORE_END====>"

# Actions that signal the agent is "looking at" a tool (opens it in context)
_TOOL_OPEN_ACTIONS = {"info", "execute"}
_TOOL_CLOSE_ACTION = "close"


# ---------------------------------------------------------------------------
# Discovery — scan message history
# ---------------------------------------------------------------------------

def discover_open_tools(messages: List[Dict]) -> Set[str]:
    """Scan message history for ``tool_store`` calls, replaying them in
    order.  Returns the set of tool names currently "open".

    ``action="info"`` or ``"execute"`` adds a toolset to the display.
    ``action="close"`` removes it (order matters — a close cancels an
    earlier open, and vice versa).
    """
    open_tools: Set[str] = set()

    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            continue

        for tc in tool_calls:
            fn = tc.get("function")
            if not fn or fn.get("name") != "tool_store":
                continue

            try:
                args = json.loads(fn.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue

            action = args.get("action", "")
            name = args.get("tool_name")
            if not name:
                continue

            if action == _TOOL_CLOSE_ACTION:
                open_tools.discard(name)
            elif action in _TOOL_OPEN_ACTIONS:
                open_tools.add(name)

    return open_tools


# ---------------------------------------------------------------------------
# Display generation
# ---------------------------------------------------------------------------

def _format_tool_display(tool: Dict) -> str:
    """Dispatch to the correct formatter based on tool type.

    First tries the polymorphic ``Tool.from_dict(tool).format_display()``
    path.  Falls back to the old per-type formatters for backwards
    compatibility when ``toolstore.tool`` is not available.
    """
    if Tool is not None:
        try:
            return Tool.from_dict(tool).format_display()
        except (ValueError, Exception):
            pass

    # ── fallback formatters (kept for backwards compatibility) ─────
    ttype = tool.get("type", "")
    if ttype == "toolset":
        return _fmt_toolset(tool)
    elif ttype == "mcp":
        return _fmt_mcp(tool)
    elif ttype == "skill":
        return _fmt_skill(tool)
    else:
        return tool.get("description", "") or json.dumps(tool, indent=2)


# ---------------------------------------------------------------------------
# Per-type formatters
# ---------------------------------------------------------------------------

def _fmt_toolset(tool: Dict) -> str:
    """Local/remote toolset: doc.md body + binding signatures."""
    doc = tool.get("doc", "") or tool.get("description", "")
    bindings = tool.get("bindings", {})

    lines = [doc.strip()] if doc.strip() else []

    if bindings:
        lines.append("")
        lines.append("Bindings:")
        for name, info in bindings.items():
            params = info.get("parameters", {})
            sig = _format_signature(name, params)
            desc = info.get("description", "")
            suffix = f" — {desc}" if desc else ""
            lines.append(f"  {sig}{suffix}")

    return "\n".join(lines)


def _fmt_mcp(tool: Dict) -> str:
    """MCP server: show all its discovered tools as bindings."""
    server_name = tool.get("mcp_server", tool["name"])
    transport = tool.get("transport", "stdio")

    lines = [
        f"MCP server — connected via {transport}",
        "",
        "Bindings:",
    ]

    # Collect all tools belonging to this MCP server from the index.
    # We need to call tool_store(search) or access the index directly.
    # For now, show just the tool itself — the full grouping requires
    # the tool_store index which we access via tool_store_tool.

    # Search for tools from this server
    try:
        raw = tool_store_tool(action="search", query=server_name)
    except Exception:
        raw = ""

    # Fallback: show the single tool if we can't get the full list
    schema = tool.get("schema", {}).get("input_schema", {})
    props = schema.get("properties", {})
    required = schema.get("required", [])

    param_strs = []
    for pname, pinfo in props.items():
        ptype = pinfo.get("type", "string")
        opt = "" if pname in required else "?"
        param_strs.append(f"{pname}{opt}: {ptype}")

    sig = f"{tool['name']}({', '.join(param_strs)})"
    desc = tool.get("description", "")
    suffix = f" — {desc}" if desc else ""
    lines.append(f"  {sig}{suffix}")

    return "\n".join(lines)


def _fmt_skill(tool: Dict) -> str:
    """Skill: full SKILL.md body — the tool response is just a short ack."""
    body = tool.get("body", "") or tool.get("description", "")
    files = tool.get("skill_files_list", [])

    lines = [body.strip()] if body.strip() else [tool.get("description", "")]
    if files:
        lines.append("")
        lines.append("Bundled files:")
        for fname in files:
            lines.append(f"  {fname}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_signature(name: str, params: Dict) -> str:
    """Format a function signature like ``get_weather(location: str, units: str = "metric")``."""
    param_strs: List[str] = []
    for pname, pinfo in params.items():
        ptype = pinfo.get("type", "string")
        if pinfo.get("required"):
            param_strs.append(f"{pname}: {ptype}")
        else:
            default = pinfo.get("default", "None")
            param_strs.append(f"{pname}: {ptype} = {default!r}")
    return f"{name}({', '.join(param_strs)})"


# ---------------------------------------------------------------------------
# Panel implementation
# ---------------------------------------------------------------------------

class ToolStorePanel(Panel):
    """Living Tool State panel for open ToolStore tools."""

    name = "toolsets"
    trigger_tools = {"tool_store"}
    block_start = TOOLSTORE_START
    block_end = TOOLSTORE_END

    def discover(self, messages):
        return discover_open_tools(messages)

    def render(self, state):
        if not state:
            return ""


        sections: List[str] = []
        for name in sorted(state):
            raw = _raw_ts(action="info", tool_name=name)
            try:
                tool = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                continue
            display = _format_tool_display(tool)
            if display:
                sections.append(f"### {name}\n{display}")

        if not sections:
            return ""

        combined = "\n\n".join(sections)
        hint = ""
        if len(state) > CONTEXT_DISPLAY_MAX_ITEMS or len(combined) > CONTEXT_DISPLAY_WARN_CHARS:
            names = ", ".join(sorted(state))
            hint = (
                f"\n⚠️ CONTEXT WARNING: You have {len(state)} toolsets open "
                f"({names}). To avoid running out of context, please close "
                f"toolsets you no longer need by calling "
                f"tool_store(action='close', tool_name='X')."
            )
        return f"{self.block_start}\n{combined}{hint}\n{self.block_end}"


