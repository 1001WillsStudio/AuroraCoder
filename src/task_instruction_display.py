"""Parse the frontend's ``[TASK INSTRUCTION]`` transport wrapper.

The markers are an internal prefix so the model sees the instruction; they
must never appear in the user bubble.  The inner text is kept on
``taskInstruction`` so the transcript can show a labeled context chip.

Imported by both the gateway store and the agent web API — keep this module
free of store/app imports.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

TASK_INSTRUCTION_START = "[TASK INSTRUCTION]"
TASK_INSTRUCTION_END = "[/TASK INSTRUCTION]"

_TASK_BLOCK_RE = re.compile(
    re.escape(TASK_INSTRUCTION_START) + r"(.*?)" + re.escape(TASK_INSTRUCTION_END) + r"\s*",
    re.DOTALL,
)


def strip_task_instruction(content: str) -> str:
    """Remove a task instruction marker block and trailing whitespace from *content*."""
    return _TASK_BLOCK_RE.sub("", content).strip()


def extract_task_instruction(content: str) -> Optional[str]:
    """Return the inner task-instruction text, or None if no marked block."""
    if not isinstance(content, str) or TASK_INSTRUCTION_START not in content:
        return None
    match = _TASK_BLOCK_RE.search(content)
    if not match:
        return None
    text = match.group(1).strip()
    return text or None


def user_message_for_frontend(content: Any) -> dict:
    """Build a user bubble: typed text only, instruction on a separate field."""
    out = {"role": "user", "content": content if content is not None else ""}
    if not isinstance(content, str) or TASK_INSTRUCTION_START not in content:
        return out
    extracted = extract_task_instruction(content)
    if extracted:
        out["taskInstruction"] = extracted
    out["content"] = strip_task_instruction(content)
    return out


def sanitize_frontend_messages(messages: List[Dict]) -> List[Dict]:
    """Strip wrappers from user bubbles; keep the instruction on ``taskInstruction``.

    Mutates *messages* in place and returns the same list.
    """
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, str) or TASK_INSTRUCTION_START not in content:
            continue
        extracted = extract_task_instruction(content)
        if extracted and not msg.get("taskInstruction"):
            msg["taskInstruction"] = extracted
        msg["content"] = strip_task_instruction(content)
    return messages
