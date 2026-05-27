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
    from toolstore.native_tool import tool_store_tool
    from toolstore.config_manager import ConfigManager
except ImportError as e:
    def tool_store_tool(**kwargs):
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


__all__ = ["tool_store_tool", "get_tool_store_tool", "get_config_path"]
