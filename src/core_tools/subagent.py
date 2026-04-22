"""
Subagent tool — delegates a self-contained task to an independent agent.

The subagent gets its own context window (fresh message list) and runs the
same agent loop with a configurable tool set.  Only its final text response
is returned to the parent, keeping the parent's context clean.
"""

import datetime
from typing import Optional

from ..config import (
    SUBAGENT_MAX_ITERATIONS,
    SUBAGENT_MAX_RESULT_CHARS,
    SYSTEM_MESSAGE_TEMPLATE,
    VNC_INSTRUCTIONS,
    TERMINAL_ENV_NOTE,
)


def _get_filtered_tools(mode: str):
    """Return tool definitions filtered by mode, excluding the subagent tool itself."""
    from ..tool_definitions import NATIVE_TOOL_DEFINITIONS, READ_ONLY_TOOLS

    defs = []
    for td in NATIVE_TOOL_DEFINITIONS:
        name = td["function"]["name"]
        if name == "subagent":
            continue
        if mode == "read_only" and name not in READ_ONLY_TOOLS:
            continue
        defs.append(td)
    return defs


def run_subagent(
    task: str,
    tools: str = "read_only",
    provider_id: Optional[str] = None,
) -> str:
    """
    Spawn a subagent that executes *task* autonomously and returns a summary.

    Args:
        task:  Detailed description of what the subagent should accomplish and
               what it should return.  The subagent has NO knowledge of the
               parent conversation — be specific.
        tools: Which tool set the subagent gets.
               "read_only"  — file reads, searches, web (default, safest)
               "all"        — full tool set except subagent itself
        provider_id: Model provider; defaults to the parent's current provider
                     (resolved at call time by the tool_definitions layer).
    """
    from ..main_flow import generate_chat_responses_stream_native

    if tools not in ("read_only", "all"):
        return f"Error: tools must be 'read_only' or 'all', got '{tools}'"

    filtered_tools = _get_filtered_tools(tools)
    if not filtered_tools:
        return "Error: no tools available for the subagent after filtering."

    system_message = SYSTEM_MESSAGE_TEMPLATE.format(
        current_time=datetime.datetime.now().isoformat(),
        vnc_instructions=VNC_INSTRUCTIONS,
        terminal_env_note=TERMINAL_ENV_NOTE,
    )

    subagent_messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": task},
    ]

    try:
        last_response = None
        for response in generate_chat_responses_stream_native(
            messages=subagent_messages,
            max_iterations=SUBAGENT_MAX_ITERATIONS,
            provider_id=provider_id,
            tools_override=filtered_tools,
        ):
            last_response = response
    except Exception as e:
        return f"Subagent error: {type(e).__name__}: {e}"

    if last_response is None:
        return "Subagent produced no response."

    # Extract the final assistant text from the last response
    messages = last_response.get("messages", [])
    status = last_response.get("status", "unknown")

    # Walk backwards to find the last assistant message with text content
    final_text = ""
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            final_text = msg["content"]
            break

    if not final_text:
        final_text = f"[Subagent finished with status '{status}' but produced no text summary.]"

    if len(final_text) > SUBAGENT_MAX_RESULT_CHARS:
        final_text = (
            final_text[:SUBAGENT_MAX_RESULT_CHARS]
            + f"\n... [truncated — {len(final_text)} chars total]"
        )

    return final_text
