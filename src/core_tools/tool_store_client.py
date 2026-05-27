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
    from toolstore.native_tool import tool_store_tool as _raw_tool_store_tool
    from toolstore.config_manager import ConfigManager
except ImportError as e:
    def _raw_tool_store_tool(**kwargs):
        return f"Error: Could not load ToolStore library. Is 'toolstore' installed? Details: {e}"

    def get_config_path() -> str:
        return "toolstore not available"
else:
    def get_config_path() -> str:
        """Return the active toolstore config directory.

        Respects ``TOOLSTORE_HOME`` env var, then the Docker persistent mount
        ``/app/data/toolstore/``, then ``~/.toolstore/`` as fallback.
        """
        return str(ConfigManager().config_dir)


def get_tool_store_tool():
    """Return the tool_store tool function."""
    return tool_store_tool


# Wrapper — intercept close locally; everything else passes through
def tool_store_tool(**kwargs):
    """ThinkWithTool wrapper around ToolStore's native tool.

    ``action="close"`` is intercepted locally for context-management
    (it never reaches the ToolStore server).  All other actions pass
    through unchanged.
    """
    action = kwargs.get("action", "")
    tool_name = kwargs.get("tool_name", "")

    if action == "close":
        return f"Closed toolset '{tool_name}' from the tool store display."

    return _raw_tool_store_tool(**kwargs)


__all__ = ["tool_store_tool", "get_tool_store_tool", "get_config_path", "get_toolstore_tools_prompt"]


def get_toolstore_tools_prompt() -> str:
    """Return a compact name-only listing of all tools available in the tool store.

    This is injected at the bottom of the system prompt so the agent can see
    what tool-store tools are available without searching first.
    Returns an empty string when no tools are registered.
    """
    try:
        from toolstore.native_tool import index_manager
        all_names = list(index_manager.index_data.get("tools", {}).keys())
        if not all_names:
            return ""
        lines = [
            "",
            "Tool store includes but is not limited to the following tools:",
        ]
        for name in sorted(all_names):
            lines.append(f"- {name}")
        return "\n".join(lines)
    except Exception:
        return ""
