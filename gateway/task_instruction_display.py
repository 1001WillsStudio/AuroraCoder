"""Parse the frontend's ``[TASK INSTRUCTION]`` transport wrapper.

The markers are an internal prefix so the model sees the instruction; they
must never appear in the user bubble.  The inner text is kept on
``taskInstruction`` so the transcript can show a labeled context chip.

This is a gateway/UI concern.  The agent core in ``src/`` should keep
copying user content through unchanged.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from gateway.attached_files import (
    ATTACHED_FILES_START,
    extract_attached_files,
    strip_attached_files,
)

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
    """Build a user bubble: typed text only, wrappers on separate fields."""
    out = {"role": "user", "content": content if content is not None else ""}
    if not isinstance(content, str):
        return out
    extracted = extract_task_instruction(content)
    if extracted:
        out["taskInstruction"] = extracted
    files = extract_attached_files(content)
    if files:
        out["attachedFiles"] = files
    stripped = strip_task_instruction(content)
    stripped = strip_attached_files(stripped)
    out["content"] = stripped
    return out


def sanitize_frontend_messages(messages: List[Dict]) -> List[Dict]:
    """Strip wrappers from user bubbles; keep chips on dedicated fields.

    Mutates *messages* in place and returns the same list.
    """
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        if TASK_INSTRUCTION_START not in content and ATTACHED_FILES_START not in content:
            continue
        extracted = extract_task_instruction(content)
        if extracted and not msg.get("taskInstruction"):
            msg["taskInstruction"] = extracted
        files = extract_attached_files(content)
        if files and not msg.get("attachedFiles"):
            msg["attachedFiles"] = files
        stripped = strip_task_instruction(content)
        msg["content"] = strip_attached_files(stripped)
    return messages
