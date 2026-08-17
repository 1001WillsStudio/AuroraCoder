"""Failed assistant turns must survive reload.

A provider failure is shown live as an error bubble with Try Again, but
the gateway used to persist only the seeded user message (status=error,
frontend_messages=[{role:user}], messages=[]). Reopening the conversation
then looked like the turn was never answered.

These tests lock the reported payload and the persist/hydrate helpers
that write the error bubble.
"""
from __future__ import annotations

import pytest

from gateway.conversation_store import (
    ConversationStore,
    build_error_frontend_message,
    ensure_error_frontend_message,
)


@pytest.fixture
def isolated_store(tmp_path):
    return ConversationStore(tmp_path / "conversations")


def test_ensure_error_frontend_message_appends_retry_bubble():
    msgs = ensure_error_frontend_message(
        [{"role": "user", "content": "e2e:error"}],
        {"message": "provider exploded", "type": "ProviderError"},
    )
    assert msgs[0] == {"role": "user", "content": "e2e:error"}
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["isError"] is True
    assert msgs[-1]["canRetry"] is True
    assert "provider exploded" in msgs[-1]["content"]
    assert msgs[-1]["isTimeout"] is False


def test_ensure_error_frontend_message_is_idempotent():
    first = ensure_error_frontend_message(
        [{"role": "user", "content": "hi"}],
        {"message": "boom"},
    )
    again = ensure_error_frontend_message(first, {"message": "other"})
    assert again == first
    assert sum(1 for m in again if m.get("isError")) == 1


def test_build_error_frontend_message_detects_timeout():
    msg = build_error_frontend_message(
        {"message": "gateway timeout (504)", "type": "TimeoutError"}
    )
    assert msg["isTimeout"] is True
    assert msg["canRetry"] is True
    assert msg["content"].startswith("Error:")


def test_build_error_frontend_message_generic_when_no_details():
    msg = build_error_frontend_message(None)
    assert "failed" in msg["content"].lower()
    assert msg["isError"] is True
    assert msg["canRetry"] is True


def test_persist_error_turn_writes_retry_bubble(isolated_store):
    cid = isolated_store.create_conversation()
    isolated_store.save_frontend_messages(
        cid, [{"role": "user", "content": "e2e:error"}]
    )

    isolated_store.persist_error_turn(
        cid, None, {"message": "Cannot connect to backend", "type": "ConnectionError"}
    )

    fe = isolated_store.get_frontend_messages(cid)
    assert fe[0]["role"] == "user"
    assert fe[0]["content"] == "e2e:error"
    assert fe[-1]["isError"] is True
    assert fe[-1]["canRetry"] is True
    assert "Cannot connect to backend" in fe[-1]["content"]


def test_error_conversation_payload_includes_retry_bubble(isolated_store):
    """Reproduce the reported GET payload: status=error, only the user bubble.

    After the fix, the API-shaped frontend_messages must end with an
    isError/canRetry assistant bubble so reload can render the failure
    and Try Again.
    """
    cid = isolated_store.create_conversation()
    isolated_store.save_frontend_messages(
        cid, [{"role": "user", "content": "e2e:error"}]
    )
    isolated_store.update_status(cid, "error")

    conv = isolated_store.get_conversation(cid)
    assert conv["status"] == "error"
    assert conv["messages"] == []
    raw_fe = isolated_store.get_frontend_messages(cid)
    assert [m.get("role") for m in raw_fe] == ["user"]
    assert raw_fe[0]["content"] == "e2e:error"

    fe = isolated_store.frontend_messages_for_status(cid, conv["status"])
    assert fe[0]["role"] == "user"
    assert fe[0]["content"] == "e2e:error"
    assert len(fe) >= 2
    assert fe[-1]["isError"] is True
    assert fe[-1]["canRetry"] is True
    assert fe[-1]["role"] == "assistant"


def test_persist_error_turn_keeps_partial_assistant_then_error(isolated_store):
    cid = isolated_store.create_conversation()
    live = [
        {"role": "user", "content": "explain this"},
        {"role": "assistant", "content": "partial...", "activities": []},
    ]
    isolated_store.persist_error_turn(
        cid, live, {"message": "stream dropped", "type": "ConnectionError"}
    )
    fe = isolated_store.get_frontend_messages(cid)
    assert [m["role"] for m in fe] == ["user", "assistant", "assistant"]
    assert fe[1]["content"] == "partial..."
    assert fe[-1]["isError"] is True
    assert "stream dropped" in fe[-1]["content"]


def test_frontend_messages_for_status_does_not_rewrite_disk(isolated_store):
    cid = isolated_store.create_conversation()
    isolated_store.save_frontend_messages(
        cid, [{"role": "user", "content": "e2e:error"}]
    )
    isolated_store.update_status(cid, "error")
    isolated_store.frontend_messages_for_status(cid, "error")
    on_disk = isolated_store.get_frontend_messages(cid)
    assert [m.get("role") for m in on_disk] == ["user"]


def test_completed_conversation_does_not_invent_error(isolated_store):
    cid = isolated_store.create_conversation()
    isolated_store.save_frontend_messages(
        cid,
        [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there", "activities": []},
        ],
    )
    isolated_store.update_status(cid, "completed")

    fe = isolated_store.frontend_messages_for_status(cid, "completed")
    assert len(fe) == 2
    assert not any(m.get("isError") for m in fe)
