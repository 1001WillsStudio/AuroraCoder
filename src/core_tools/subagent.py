"""
Subagent tool — delegates a self-contained task to an independent agent.

Goes through the same conversation-server pipeline as a normal user request.
The only extra metadata is parent_id / conv_type which the conversation
server stores for linking; the backend never sees them.

Subagents are read-only (no terminal/write access) for safety, and are
stoppable — cancelling the parent stream cascades to all active subagents.
"""

import json
from typing import Dict, Any, Tuple
import logging
import os
import threading
import uuid

import requests

from ..config import SUBAGENT_MAX_RESULT_CHARS

logger = logging.getLogger(__name__)

CONVO_SERVER_URL = os.environ.get("CONVO_SERVER_URL", "http://localhost:8081")

# Track active subagent runs so they can be cancelled from outside.
_active_subagents: dict[str, tuple[threading.Event, requests.Response | None, str]] = {}
_active_lock = threading.Lock()


def cancel_active_subagents(parent_conversation_id: str | None = None) -> None:
    """
    Cancel running subagent connections.

    Args:
        parent_conversation_id: If provided, only cancel subagents belonging to this
            parent conversation. If None, cancel ALL subagents (deprecated escape hatch).
    """
    with _active_lock:
        if parent_conversation_id is not None:
            items = [(cid, val) for cid, val in _active_subagents.items()
                     if val[2] == parent_conversation_id]
            for cid in items:
                del _active_subagents[cid[0]]
        else:
            items = list(_active_subagents.items())
            _active_subagents.clear()

    for child_id, (cancel_evt, resp, _parent_cid) in items:
        cancel_evt.set()
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        try:
            requests.post(
                f"{CONVO_SERVER_URL}/api/conversations/{child_id}/cancel",
                timeout=5,
            )
        except Exception:
            pass
        logger.info(f"[subagent] Cancelled child {child_id[:8]}")


def run_subagent(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Spawn a subagent that executes *task* autonomously and returns a summary.

    Args are passed as a dict:
        task:  Detailed description of what the subagent should accomplish.
               The subagent has NO knowledge of the parent conversation.
        provider_id: Model provider; defaults to the parent's current provider.
        tool_call_id: The parent's tool_call id for this call, so the frontend
                      can correlate subagent events with the originating tool.
    """
    task = arguments["task"]
    provider_id = arguments.get("provider_id")
    tool_call_id = arguments.get("tool_call_id")
    # Strip execution-only tool_call_id from the returned arguments
    arguments = {k: v for k, v in arguments.items() if k != "tool_call_id"}

    from ..code_tools.file_operations import _current_conversation_id as parent_cid

    tools = "read_only"

    child_id = str(uuid.uuid4())
    cancel_event = threading.Event()

    # Register this subagent run for external cancellation
    with _active_lock:
        _active_subagents[child_id] = (cancel_event, None, parent_cid)

    body: dict = {
        "message": task,
        "conversation_id": child_id,
        "tools": tools,
        "conv_type": "subagent",
        "parent_id": parent_cid,
        "tool_call_id": tool_call_id,
    }
    if provider_id:
        body["provider"] = provider_id

    try:
        resp = requests.post(
            f"{CONVO_SERVER_URL}/api/chat",
            json=body,
            stream=True,
            timeout=(5, 60),
        )

        # Store the response so cancel_active_subagents() can close it
        with _active_lock:
            if child_id in _active_subagents:
                _active_subagents[child_id] = (cancel_event, resp, parent_cid)

        if resp.status_code != 200:
            return f"Subagent error: conversation server returned {resp.status_code}: {resp.text[:500]}", arguments

        final_text = ""
        final_status = "unknown"

        for line in resp.iter_lines(decode_unicode=True):
            if cancel_event.is_set():
                logger.info(f"[subagent] Cancelled during streaming for {child_id[:8]}")
                break

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
        if cancel_event.is_set():
            logger.info(f"[subagent] Connection closed by cancellation for {child_id[:8]}")
        else:
            logger.exception(f"Subagent HTTP error for {child_id[:8]}")
            return f"Subagent error: {type(e).__name__}: {e}", arguments

    finally:
        # Unregister this subagent run
        with _active_lock:
            _active_subagents.pop(child_id, None)

    if cancel_event.is_set() and not final_text:
        return "[Subagent was stopped by user.]", arguments

    if not final_text:
        final_text = f"[Subagent finished with status '{final_status}' but produced no text summary.]"

    if len(final_text) > SUBAGENT_MAX_RESULT_CHARS:
        final_text = (
            final_text[:SUBAGENT_MAX_RESULT_CHARS]
            + f"\n... [truncated — {len(final_text)} chars total]"
        )

    return final_text, arguments
