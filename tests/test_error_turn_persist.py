"""Failed assistant turns must survive reload.

A provider failure used to persist only the seeded user message
(status=error, frontend_messages=[{role:user}], messages=[]). Reopening
the conversation then looked unanswered.
"""
from __future__ import annotations

from pathlib import Path

from gateway.conversation_store import (
    ConversationStore,
    ensure_error_frontend_message,
    messages_for_retry,
)


def test_reported_error_payload_gets_retry_bubble():
    """The explorer payload: status=error and only the user bubble."""
    raw = [{"role": "user", "content": "e2e:error"}]
    fe = ensure_error_frontend_message(raw, {"message": "provider exploded"})
    assert fe[0] == {"role": "user", "content": "e2e:error"}
    assert fe[-1]["isError"] is True
    assert fe[-1]["canRetry"] is True
    assert "provider exploded" in fe[-1]["content"]


def test_ensure_error_frontend_message_is_idempotent():
    first = ensure_error_frontend_message([{"role": "user", "content": "hi"}], {"message": "boom"})
    assert ensure_error_frontend_message(first, {"message": "other"}) == first


def test_error_bubble_survives_store_round_trip(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    cid = store.create_conversation()
    store.save_frontend_messages(cid, [{"role": "user", "content": "e2e:error"}])
    store.update_status(cid, "error")
    store.save_frontend_messages(
        cid,
        ensure_error_frontend_message(
            store.get_frontend_messages(cid),
            {"message": "Cannot connect to backend"},
        ),
    )
    fe = store.get_frontend_messages(cid)
    assert fe[0]["content"] == "e2e:error"
    assert fe[-1]["isError"] is True
    assert fe[-1]["canRetry"] is True
    assert "Cannot connect to backend" in fe[-1]["content"]


def test_retry_does_not_append_a_second_user_turn():
    """Fail-fast after a prior completed turn: add the new user line once."""
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    retried = messages_for_retry(history, "e2e:error")
    assert [m["role"] for m in retried] == ["user", "assistant", "user"]
    assert [m["content"] for m in retried if m["role"] == "user"] == ["hello", "e2e:error"]
    assert messages_for_retry(retried, "e2e:error") == retried


def test_retry_keeps_completed_tool_round():
    """A finished tool-call / tool-result round is the history to retry from."""
    history = [
        {"role": "user", "content": "e2e:error"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "file contents"},
    ]
    assert messages_for_retry(history, "e2e:error") == history


def test_retry_drops_incomplete_tool_round():
    history = [
        {"role": "user", "content": "e2e:error"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "function": {"name": "read_file", "arguments": "{}"}},
                {"id": "c2", "function": {"name": "grep_search", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    assert messages_for_retry(history, "e2e:error") == history[:1]


def test_retry_drops_partial_assistant_keeps_prior_tool_round():
    history = [
        {"role": "user", "content": "e2e:error"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        {"role": "assistant", "content": "partial..."},
    ]
    assert messages_for_retry(history, "e2e:error") == history[:3]


def test_retry_first_turn_when_raw_history_is_empty():
    assert messages_for_retry([], "e2e:error") == [
        {"role": "user", "content": "e2e:error"}
    ]


def test_try_again_uses_retry_flag_and_live_raw_history():
    src = (Path(__file__).resolve().parent.parent / "frontend" / "src" / "App.jsx").read_text(
        encoding="utf-8"
    )
    assert "retry: true" in src
    assert "const isRetry = Boolean(options.retry)" in src
    assert "Prefer live raw history" in src


def test_retry_matches_user_text_after_task_instruction_wrapper():
    wrapped = "[TASK INSTRUCTION]\nbe brief\n[/TASK INSTRUCTION]\n\ne2e:error"
    history = [{"role": "user", "content": wrapped}]
    assert messages_for_retry(history, "e2e:error") == history
