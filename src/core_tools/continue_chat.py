"""continue_as_new_chat — the tool call itself is the signal. The proxy handles everything."""

from typing import Dict, Any, Tuple


def continue_as_new_chat(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """The proxy detects this tool call and creates the new conversation."""
    return "Continuing in a new chat with fresh context.", arguments
