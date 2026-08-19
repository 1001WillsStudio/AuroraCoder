"""Try Again after a first-turn provider 500 must stay on the same chat.

The first send still omits conversation_id (the gateway allocates it).
A 500 never emits messages/done, so the UI used to retry with a null id
and the gateway minted a second history item. The fix is frontend-only:
keep the X-Conversation-ID the gateway already returns, and send retry
without a second user bubble.
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
STREAM_UTILS = ROOT / "frontend" / "src" / "utils" / "streamUtils.js"


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


def test_first_send_does_not_mint_a_client_conversation_id():
    """First message of a chat still sends conversation_id null."""
    src = APP_JSX.read_text(encoding="utf-8")
    send_fn = src.split("const handleSend =", 1)[1].split("const handleInterruptSend", 1)[0]
    assert "newConversationId" not in send_fn
    assert "crypto.randomUUID()" not in send_fn
    assert "conversationIdRef.current || conversationId" in send_fn
    utils = STREAM_UTILS.read_text(encoding="utf-8")
    assert "function newConversationId" not in utils


def test_stream_chat_adopts_conversation_id_from_response_header():
    """The gateway already sets X-Conversation-ID; the UI must keep it."""
    src = API_JS.read_text(encoding="utf-8")
    assert "X-Conversation-ID" in src
    assert "onConversationId" in src


def test_try_again_uses_retry_flag_and_does_not_append_user_bubble():
    src = APP_JSX.read_text(encoding="utf-8")
    assert "retry: true" in src
    assert "const isRetry = Boolean(options.retry)" in src
    send_fn = src.split("const handleSend =", 1)[1].split("const handleInterruptSend", 1)[0]
    retry_branch = send_fn.split("if (isRetry)", 1)[1].split("} else {", 1)[0]
    assert "role: 'user'" not in retry_branch
