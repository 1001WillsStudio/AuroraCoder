"""Try Again after a first-turn provider 500 must stay on the same chat.

Try Again is Continue, not a new send: it posts the transcript the user
already has (message=null) with the conversation id from X-Conversation-ID.
A regular send still omits conversation_id so the gateway allocates it.
"""
from __future__ import annotations

from pathlib import Path

from gateway.conversation_store import ConversationStore, ensure_error_frontend_message


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


def test_resending_transcript_does_not_fork_or_duplicate_user(tmp_path):
    """Same id + existing messages + no new message → one chat, one user line."""
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
        "message": None,
        "conversation_id": cid,
        "messages": [{"role": "user", "content": "e2e:error"}],
    }
    reused = store.create_conversation(conversation_id=body["conversation_id"])
    assert reused == cid
    if body.get("messages"):
        store.save_messages(cid, body["messages"])
    if body.get("message"):
        store.seed_frontend_user_message(cid, body["message"])

    assert len(store.list_conversations()) == 1
    user_msgs = [m for m in store.get_frontend_messages(cid) if m.get("role") == "user"]
    assert [m.get("content") for m in user_msgs] == ["e2e:error"]


def test_try_again_resends_existing_transcript_like_continue():
    """Try Again must not call handleSend or send a new user message."""
    retry = _handle_retry_src()
    send = _handle_send_src()
    assert "handleSend(" not in retry
    assert "retry: true" not in retry
    assert "if (!cid) return" in retry
    assert "streamChat(null, cid," in retry
    assert "const isRetry" not in send
    assert "retry: true" not in send


def test_stream_chat_adopts_conversation_id_from_response_header():
    """The gateway already sets X-Conversation-ID; the UI must keep it."""
    src = API_JS.read_text(encoding="utf-8")
    assert "X-Conversation-ID" in src
    assert "onConversationId" in src


def test_try_again_does_not_append_user_bubble():
    retry = _handle_retry_src()
    send = _handle_send_src()
    assert "userBubble" not in retry
    assert "role: 'user'" in send
