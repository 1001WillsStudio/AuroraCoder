"""continue_as_new_chat — the tool call itself is the signal. The proxy handles everything."""

from typing import Dict, Any, Tuple

from ..config import CONTINUATION_TOOL_RESULT


def continue_as_new_chat(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """The proxy detects this tool call and creates the new conversation."""
    if not isinstance(arguments.get("prompt"), str) or not arguments["prompt"].strip():
        return "Error: provide a handoff prompt before continuing.", arguments
    return CONTINUATION_TOOL_RESULT, arguments
