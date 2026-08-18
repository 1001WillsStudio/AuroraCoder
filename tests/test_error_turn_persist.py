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


# Exact string the OpenAI client produces for the mock-provider 500 that the
# explorer triggers by sending "e2e:error". Shown verbatim in the assistant
# bubble before sanitisation.
_RAW_PROVIDER_500 = (
    "Error code: 500 - {'error': {'message': 'mock provider: simulated "
    "upstream failure', 'type': 'mock_error'}}"
)


def test_reported_error_payload_gets_retry_bubble():
    """The explorer payload: status=error and only the user bubble."""
    raw = [{"role": "user", "content": "e2e:error"}]
    fe = ensure_error_frontend_message(raw, {"message": "provider exploded"})
    assert fe[0] == {"role": "user", "content": "e2e:error"}
    assert fe[-1]["isError"] is True
    assert fe[-1]["canRetry"] is True
    assert "provider exploded" in fe[-1]["content"]


def test_raw_openai_error_blob_is_not_shown_in_error_bubble():
    """Provider 500s must not leak the SDK/JSON dump into the chat bubble."""
    fe = ensure_error_frontend_message(
        [{"role": "user", "content": "e2e:error"}],
        {"message": _RAW_PROVIDER_500, "type": "InternalServerError"},
    )
    content = fe[-1]["content"]
    assert fe[-1]["isError"] is True
    assert fe[-1]["canRetry"] is True
    assert "mock provider" not in content
    assert "simulated upstream" not in content
    assert "Error code:" not in content
    assert "{'error'" not in content
    assert "model provider failed" in content.lower()


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


def test_retry_keeps_existing_transcript():
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "e2e:error"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    assert messages_for_retry(history, "e2e:error") == history


def test_retry_seeds_user_only_when_raw_history_is_empty():
    assert messages_for_retry([], "e2e:error") == [
        {"role": "user", "content": "e2e:error"}
    ]
    assert messages_for_retry([{"role": "user", "content": "e2e:error"}], "e2e:error") == [
        {"role": "user", "content": "e2e:error"}
    ]


def test_try_again_uses_retry_flag():
    src = (Path(__file__).resolve().parent.parent / "frontend" / "src" / "App.jsx").read_text(
        encoding="utf-8"
    )
    assert "retry: true" in src
    assert "const isRetry = Boolean(options.retry)" in src
