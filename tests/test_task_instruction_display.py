"""Regression: task-instruction wrappers must not leak into the user bubble.

The frontend prepends a marked block to the first message of a new chat so
the model sees the instruction.  The sidebar title already strips that
block; the transcript must do the same.  Reported (explorer): the user
bubble rendered

    [TASK INSTRUCTION] ALWAYS reply in ALL CAPS. ... [/TASK INSTRUCTION] Ping

while the sidebar title correctly showed only ``Ping``.
"""
from __future__ import annotations

import pytest

from gateway.conversation_store import (
    ConversationStore,
    _extract_title,
    sanitize_frontend_messages,
    strip_task_instruction,
)


WRAPPED_PING = (
    "[TASK INSTRUCTION]\n"
    "ALWAYS reply in ALL CAPS. This is a test instruction.\n"
    "[/TASK INSTRUCTION]\n\n"
    "Ping"
)


# --------------------------------------------------------------------------- strip / title
def test_strip_task_instruction_leaves_only_user_text():
    assert strip_task_instruction(WRAPPED_PING) == "Ping"


def test_strip_task_instruction_is_noop_without_markers():
    assert strip_task_instruction("Ping") == "Ping"


def test_extract_title_uses_user_text_not_instruction():
    title = _extract_title([{"role": "user", "content": WRAPPED_PING}])
    assert title == "Ping"
    assert "TASK INSTRUCTION" not in title


# --------------------------------------------------------------------------- frontend-message sanitizer
def test_sanitize_frontend_messages_strips_user_bubble_only():
    assistant = {"role": "assistant", "content": "HELLO", "activities": []}
    messages = [
        {"role": "user", "content": WRAPPED_PING},
        assistant,
    ]
    out = sanitize_frontend_messages(messages)
    assert out[0]["content"] == "Ping"
    assert "[TASK INSTRUCTION]" not in out[0]["content"]
    assert out[1] is assistant
    assert out[1]["content"] == "HELLO"


def test_save_and_get_frontend_messages_hide_task_instruction(tmp_path):
    store = ConversationStore(storage_dir=tmp_path)
    cid = store.create_conversation()
    store.save_frontend_messages(cid, [{"role": "user", "content": WRAPPED_PING}])

    saved = store.get_frontend_messages(cid)
    assert saved[0]["content"] == "Ping"
    assert "[TASK INSTRUCTION]" not in saved[0]["content"]
    assert store.get_conversation(cid)["title"] == "Ping"


# --------------------------------------------------------------------------- UI conversion (the live SSE path)
def test_convert_messages_for_frontend_hides_task_instruction_in_user_bubble():
    from src.web_api.app import convert_messages_for_frontend

    out = convert_messages_for_frontend([
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": WRAPPED_PING},
        {"role": "assistant", "content": "PONG"},
    ])
    user_msgs = [m for m in out if m["role"] == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["content"] == "Ping"
    assert "[TASK INSTRUCTION]" not in user_msgs[0]["content"]
    assert not any(m["role"] == "system" for m in out)
    assert any(m["role"] == "assistant" and m["content"] == "PONG" for m in out)


def test_convert_messages_for_frontend_leaves_plain_user_text():
    from src.web_api.app import convert_messages_for_frontend

    out = convert_messages_for_frontend([{"role": "user", "content": "Ping"}])
    assert out == [{"role": "user", "content": "Ping"}]
