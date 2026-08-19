"""Try Again after a first-turn provider 500 must stay on the same chat.

Try Again is its own path (not handleSend with a flag). A regular send
still omits conversation_id so the gateway allocates it; the UI keeps
X-Conversation-ID. Retry requires that id, sends retry:true, and does
not append another user bubble.
"""
from __future__ import annotations

from pathlib import Path

from gateway.conversation_store import (
    ConversationStore,
    ensure_error_frontend_message,
    messages_for_retry,
)


ROOT = Path(__file__).resolve().parent.parent
APP_JSX = ROOT / "frontend" / "src" / "App.jsx"
API_JS = ROOT / "frontend" / "src" / "services" / "api.js"


def _handle_retry_src() -> str:
    src = APP_JSX.read_text(encoding="utf-8")
    start = src.index("const handleRetry =")
    end = src.index("// ── Render", start)
    return src[start:end]


def _handle_send_src() -> str:
    src = APP_JSX.read_text(encoding="utf-8")
    return src.split("const handleSend =", 1)[1].split("const handleInterruptSend", 1)[0]


def test_retry_same_id_does_not_fork_or_duplicate_user(tmp_path):
    """Same id + retry flag → one chat, one user line."""
    store = ConversationStore(tmp_path / "conversations")
    cid = store.create_conversation()
    store.seed_frontend_user_message(cid, "e2e:error")
    store.save_frontend_messages(
        cid,
        ensure_error_frontend_message(
            store.get_frontend_messages(cid),
            {"message": "Backend returned 500: provider exploded"},
        ),
    )
    assert len(store.list_conversations()) == 1

    body = {
        "message": "e2e:error",
        "conversation_id": cid,
        "retry": True,
        "messages": [],
    }
    reused = store.create_conversation(conversation_id=body["conversation_id"])
    assert reused == cid
    prior = body.get("messages") or store.get_messages(cid)
    retried = messages_for_retry(prior, body.get("message") or "")
    if body.get("retry"):
        body["message"] = None
    if body.get("message"):
        store.seed_frontend_user_message(cid, body["message"])

    assert retried in ([], [{"role": "user", "content": "e2e:error"}])
    assert len(store.list_conversations()) == 1
    user_msgs = [m for m in store.get_frontend_messages(cid) if m.get("role") == "user"]
    assert [m.get("content") for m in user_msgs] == ["e2e:error"]


def test_try_again_is_not_a_flagged_regular_send():
    """Try Again must not call handleSend — that path is for a new user turn."""
    retry = _handle_retry_src()
    send = _handle_send_src()
    assert "handleSend(" not in retry
    assert "retry: true" in retry
    assert "if (!cid) return" in retry
    assert "streamChat(message, cid," in retry
    assert "const isRetry" not in send
    assert "retry: true" not in send


def test_stream_chat_adopts_conversation_id_from_response_header():
    """The gateway already sets X-Conversation-ID; the UI must keep it."""
    src = API_JS.read_text(encoding="utf-8")
    assert "X-Conversation-ID" in src
    assert "onConversationId" in src


def test_try_again_does_not_append_user_bubble():
    retry = _handle_retry_src()
    assert "role: 'user'" not in retry
    send = _handle_send_src()
    assert "role: 'user'" in send
