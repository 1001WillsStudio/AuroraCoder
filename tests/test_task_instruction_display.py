"""Regression: task-instruction wrappers must not leak into the user bubble.

The frontend prepends a marked block to the first message of a new chat so
the model sees the instruction.  The sidebar title already strips that
block; the transcript must do the same, and keep the inner text on a
separate ``taskInstruction`` field so a labeled context chip can show
what prompt was applied.  Reported (explorer): the user bubble rendered

    [TASK INSTRUCTION] ALWAYS reply in ALL CAPS. ... [/TASK INSTRUCTION] Ping

while the sidebar title correctly showed only ``Ping``.
"""
from __future__ import annotations

from gateway.conversation_store import ConversationStore, _extract_title
from src.task_instruction_display import (
    extract_task_instruction,
    sanitize_frontend_messages,
    strip_task_instruction,
    user_message_for_frontend,
)


INSTRUCTION = "ALWAYS reply in ALL CAPS. This is a test instruction."

WRAPPED_PING = (
    "[TASK INSTRUCTION]\n"
    f"{INSTRUCTION}\n"
    "[/TASK INSTRUCTION]\n\n"
    "Ping"
)


# --------------------------------------------------------------------------- strip / extract / title
def test_strip_task_instruction_leaves_only_user_text():
    assert strip_task_instruction(WRAPPED_PING) == "Ping"


def test_strip_task_instruction_is_noop_without_markers():
    assert strip_task_instruction("Ping") == "Ping"


def test_extract_task_instruction_returns_inner_text():
    assert extract_task_instruction(WRAPPED_PING) == INSTRUCTION


def test_extract_task_instruction_is_none_without_markers():
    assert extract_task_instruction("Ping") is None


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
    assert out[0]["taskInstruction"] == INSTRUCTION
    assert "TASK INSTRUCTION" not in out[0]["taskInstruction"]
    assert out[1] is assistant
    assert out[1]["content"] == "HELLO"


def test_sanitize_frontend_messages_preserves_existing_chip_field():
    messages = [{
        "role": "user",
        "content": WRAPPED_PING,
        "taskInstruction": "already captured",
    }]
    out = sanitize_frontend_messages(messages)
    assert out[0]["content"] == "Ping"
    assert out[0]["taskInstruction"] == "already captured"


def test_sanitize_frontend_messages_is_idempotent_with_chip():
    messages = [{"role": "user", "content": WRAPPED_PING}]
    first = sanitize_frontend_messages(messages)
    second = sanitize_frontend_messages(first)
    assert second[0]["content"] == "Ping"
    assert second[0]["taskInstruction"] == INSTRUCTION


def test_save_and_get_frontend_messages_hide_wrapper_keep_chip(tmp_path):
    store = ConversationStore(storage_dir=tmp_path)
    cid = store.create_conversation()
    store.save_frontend_messages(cid, [{"role": "user", "content": WRAPPED_PING}])

    saved = store.get_frontend_messages(cid)
    assert saved[0]["content"] == "Ping"
    assert "[TASK INSTRUCTION]" not in saved[0]["content"]
    assert saved[0]["taskInstruction"] == INSTRUCTION
    assert store.get_conversation(cid)["title"] == "Ping"


# --------------------------------------------------------------------------- UI conversion (the live SSE user-bubble path)
def test_user_message_for_frontend_hides_wrapper_and_exposes_chip():
    out = user_message_for_frontend(WRAPPED_PING)
    assert out["role"] == "user"
    assert out["content"] == "Ping"
    assert "[TASK INSTRUCTION]" not in out["content"]
    assert out["taskInstruction"] == INSTRUCTION


def test_user_message_for_frontend_leaves_plain_user_text():
    out = user_message_for_frontend("Ping")
    assert out == {"role": "user", "content": "Ping"}
    assert "taskInstruction" not in out
