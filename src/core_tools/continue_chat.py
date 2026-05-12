"""continue_as_new_chat — the tool call itself is the signal. The proxy handles everything."""


def continue_as_new_chat(prompt: str) -> str:
    """The proxy detects this tool call and creates the new conversation."""
    return "Continuing in a new chat with fresh context."
