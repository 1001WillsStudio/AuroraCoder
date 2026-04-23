"""
Subagent tool — delegates a self-contained task to an independent agent.

Goes through the same conversation-server pipeline as a normal user request.
The only extra metadata is parent_id / conv_type which the conversation
server stores for linking; the backend never sees them.
"""

import json
import logging
import os
import uuid

import requests

from ..config import SUBAGENT_MAX_RESULT_CHARS

logger = logging.getLogger(__name__)

CONVO_SERVER_URL = os.environ.get("CONVO_SERVER_URL", "http://localhost:8081")


def run_subagent(
    task: str,
    provider_id: str | None = None,
) -> str:
    """
    Spawn a subagent that executes *task* autonomously and returns a summary.

    Args:
        task:  Detailed description of what the subagent should accomplish.
               The subagent has NO knowledge of the parent conversation.
        provider_id: Model provider; defaults to the parent's current provider.
    """
    from ..code_tools.file_operations import _current_conversation_id as parent_cid

    tools = "read_only"

    child_id = str(uuid.uuid4())

    body: dict = {
        "message": task,
        "conversation_id": child_id,
        "tools": tools,
        "conv_type": "subagent",
        "parent_id": parent_cid,
    }
    if provider_id:
        body["provider"] = provider_id

    try:
        resp = requests.post(
            f"{CONVO_SERVER_URL}/api/chat",
            json=body,
            stream=True,
            timeout=None,
        )

        if resp.status_code != 200:
            return f"Subagent error: conversation server returned {resp.status_code}: {resp.text[:500]}"

        final_text = ""
        final_status = "unknown"

        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue

            if data.get("status"):
                final_status = data["status"]

            for msg in reversed(data.get("raw_messages", [])):
                if msg.get("role") == "assistant" and msg.get("content"):
                    final_text = msg["content"]
                    break

    except Exception as e:
        logger.exception(f"Subagent HTTP error for {child_id[:8]}")
        return f"Subagent error: {type(e).__name__}: {e}"

    if not final_text:
        final_text = f"[Subagent finished with status '{final_status}' but produced no text summary.]"

    if len(final_text) > SUBAGENT_MAX_RESULT_CHARS:
        final_text = (
            final_text[:SUBAGENT_MAX_RESULT_CHARS]
            + f"\n... [truncated — {len(final_text)} chars total]"
        )

    return final_text
