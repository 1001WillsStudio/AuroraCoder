try:
    from toolstore.native_tool import tool_store_tool
except ImportError as e:
    # If we can't import, define a dummy function that reports the error
    def tool_store_tool(**kwargs):
        return f"Error: Could not load ToolStore library. Is 'toolstore' installed in the current environment? Details: {e}"
