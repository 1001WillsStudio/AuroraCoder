"""
ToolStore integration for ThinkWithTool.

Provides the ``tool_store_tool`` function that agents can call
to search and execute tools published in the ToolStore registry.

Persistence
-----------
The toolstore stores its config (MCP servers, credentials, skills)
in a persistent directory so it survives container restarts.

Resolution order (first match wins):
  1. ``TOOLSTORE_HOME`` environment variable
  2. ``/app/data/toolstore/``  (standard ThinkWithTool Docker mount)
  3. ``~/.toolstore/``         (fallback, ephemeral in Docker)

To check where config is stored at runtime::

    >>> from core_tools.tool_store_client import get_config_path
    >>> print(get_config_path())
    /app/data/toolstore
"""

try:
    from toolstore.native_tool import (
        tool_store_tool as _raw_tool_store_tool,
    )
    from toolstore.native_tool import (
        get_primary_tool_schemas as _native_get_primary_schemas,
        get_primary_tool_prompt as _native_get_primary_prompt,
        execute_tool_direct as _native_execute_tool_direct,
        prefetch_primary_tools as _native_prefetch_primary_tools,
    )
    from toolstore.config_manager import ConfigManager
except ImportError as e:
    # ── ToolStore library is NOT installed ──────────────────────────
    def _raw_tool_store_tool(**kwargs):
        return (
            f"Error: Could not load ToolStore library. "
            f"Is 'toolstore' installed? Details: {e}"
        )

    def get_config_path() -> str:
        return "toolstore not available"

    def get_primary_tool_schemas() -> list:
        return []

    def _primary_tool_listing() -> str:
        return ""

    def execute_tool_direct(name: str, kwargs: dict) -> str:
        return (
            f"Error: ToolStore is not available — cannot execute '{name}'."
        )

    def prefetch_primary_tools() -> int:
        return 0

else:
    # ── ToolStore IS installed — wire real implementations ──────────
    def get_config_path() -> str:
        """Return the active toolstore config directory.

        Respects ``TOOLSTORE_HOME`` env var, then the Docker persistent mount
        ``/app/data/toolstore/``, then ``~/.toolstore/`` as fallback.
        """
        return str(ConfigManager().config_dir)

    def get_primary_tool_schemas() -> list:
        return _native_get_primary_schemas()

    def _primary_tool_listing() -> str:
        return _native_get_primary_prompt()

    def execute_tool_direct(name: str, kwargs: dict) -> str:
        return _native_execute_tool_direct(name, kwargs)

    def prefetch_primary_tools() -> int:
        return _native_prefetch_primary_tools()


def get_tool_store_tool():
    """Return the tool_store tool function."""
    return tool_store_tool


# Wrapper — intercept close + skill execute/info locally; everything else passes through
def tool_store_tool(**kwargs):
    """ThinkWithTool wrapper around ToolStore's native tool.

    ``action="close"`` is intercepted locally for context-management
    (it never reaches the ToolStore server).

    ``action="execute"`` and ``action="info"`` for skills return a short
    acknowledgement — the full body lives in the ``<====TOOLSTORE_START/END>``
    display block managed by the toolset context manager.
    """
    action = kwargs.get("action", "")
    tool_name = kwargs.get("tool_name", "")

    if action == "close":
        return f"Closed toolset '{tool_name}' from the tool store display."

    # Skill execute/info — body is in the toolstore display block.
    if action in ("execute", "info") and tool_name.startswith("skill:"):
        skill_name = tool_name[len("skill:"):]
        return f"Skill '{skill_name}' loaded — see toolstore display below."

    return _raw_tool_store_tool(**kwargs)


def get_primary_tools_prompt() -> str:
    """Return the primary-tool block for the system message.

    Primary tools are callable directly by the LLM — their schemas are
    injected into the ``tools[]`` array as first‑class citizens so the
    model can invoke them just like ``google_search`` or ``web_browser``.

    Returns a formatted block listing each primary tool with its
    description, or an empty string when no primary tools are configured.
    """
    raw = _primary_tool_listing()
    if not raw:
        return ""
    return (
        "\n\n**Primary Tools** (call these directly — no ``tool_store`` needed):\n"
        + raw
    )


def get_toolstore_tools_prompt() -> str:
    """Return a compact name‑only listing of *secondary* ToolStore tools.

    These tools must be accessed through ``tool_store(action=…)``.
    Primary tools are listed separately via :func:`get_primary_tools_prompt`.
    """
    try:
        from toolstore import get_secondary_tool_names

        secondary_names = sorted(get_secondary_tool_names())
        if not secondary_names:
            return ""

        lines = [
            "",
            "Tool store includes but is not limited to the following tools:",
        ]
        for name in secondary_names:
            lines.append(f"- {name}")
        return "\n".join(lines)
    except Exception:
        return ""


__all__ = [
    "tool_store_tool",
    "get_tool_store_tool",
    "get_config_path",
    "get_toolstore_tools_prompt",
    "get_primary_tools_prompt",
    "get_primary_tool_schemas",
    "execute_tool_direct",
    "prefetch_primary_tools",
]
