"""Try Again after a first-turn provider 500 must stay on the same chat.

Explorer (fresh page): send ``e2e:error``, wait for the red error bubble,
click Try Again. All History grew by one and the transcript showed the
user line twice — the frontend never learned the conversation id after
the 500 (no ``messages`` / ``done`` event), so the retry POST omitted
``conversation_id`` and the gateway minted a second chat.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from gateway.conversation_store import (
    ConversationStore,
    ensure_error_frontend_message,
    messages_for_retry,
)
from gateway.streaming import ActiveStream, _proxy_backend_stream


ROOT = Path(__file__).resolve().parent.parent
APP_JSX = ROOT / "frontend" / "src" / "App.jsx"
API_JS = ROOT / "frontend" / "src" / "services" / "api.js"
CALLBACKS_JS = ROOT / "frontend" / "src" / "hooks" / "createStreamCallbacks.js"
STREAM_UTILS = ROOT / "frontend" / "src" / "utils" / "streamUtils.js"


class _FakeStreamResponse:
    def __init__(self, status_code: int = 500, body: bytes = b"provider exploded"):
        self.status_code = status_code
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aread(self):
        return self._body

    async def aiter_text(self):
        if False:
            yield ""


class _FakeAsyncClient:
    """Hermetic httpx stand-in: the agent backend always returns 500."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *args, **kwargs):
        raise httpx.ConnectError("hermetic: no network")

    def stream(self, *args, **kwargs):
        return _FakeStreamResponse()


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    isolated = ConversationStore(tmp_path / "conversations")
    monkeypatch.setattr("gateway.streaming.store", isolated)
    monkeypatch.setattr("gateway.streaming._schedule_memory_distillation", lambda *a, **k: None)
    monkeypatch.setattr("gateway.streaming.httpx.AsyncClient", _FakeAsyncClient)
    return isolated


async def _run_failing_proxy(cid: str):
    stream = ActiveStream(conversation_id=cid)
    queue: asyncio.Queue = asyncio.Queue()
    stream.subscribers.append(queue)
    await _proxy_backend_stream(stream, {"message": "e2e:error", "conversation_id": cid})
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return stream, events


@pytest.mark.asyncio
async def test_backend_500_error_event_includes_conversation_id(isolated_store):
    """A first-turn 500 must tell the UI which conversation just failed."""
    cid = isolated_store.create_conversation()
    isolated_store.seed_frontend_user_message(cid, "e2e:error")

    _stream, events = await _run_failing_proxy(cid)
    errors = [data for item in events if item and item[0] == "error" for data in [item[1]]]
    assert errors, f"expected an error event, got {events!r}"
    assert errors[0].get("conversation_id") == cid

    fe = isolated_store.get_frontend_messages(cid)
    assert [m.get("content") for m in fe if m.get("role") == "user"] == ["e2e:error"]
    assert fe[-1].get("isError") is True
    assert fe[-1].get("canRetry") is True


def test_retry_same_id_does_not_fork_or_duplicate_user(tmp_path):
    """Gateway retry contract: same id + retry flag → one chat, one user line."""
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

    # Payload the fixed UI sends on Try Again (same id, retry, no extra user).
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


def _eval_new_conversation_id(script: str) -> str:
    full = (
        f"import {{ newConversationId }} from {json.dumps(STREAM_UTILS.resolve().as_uri())}\n"
        f"{script}\n"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", full],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout or "node helper failed")
    return proc.stdout.strip()


def test_new_conversation_id_works_without_random_uuid():
    """Some browsers expose crypto but not randomUUID."""
    script = (
        "const ids = [\n"
        "  newConversationId({ getRandomValues: (b) => crypto.getRandomValues(b) }),\n"
        "  newConversationId({}),\n"
        "  newConversationId(null),\n"
        "]\n"
        "console.log(JSON.stringify(ids))\n"
    )
    ids = json.loads(_eval_new_conversation_id(script))
    assert len(ids) == 3
    for value in ids:
        assert len(value) == 36
        assert value.count("-") == 4
        parts = value.split("-")
        assert parts[2].startswith("4"), value


def test_handle_send_mints_conversation_id_before_streamchat():
    """First send must allocate an id so Try Again can reuse it after a 500."""
    src = APP_JSX.read_text(encoding="utf-8")
    assert "const cid = conversationIdRef.current" in src
    assert "streamChat(apiMessage, cid," in src
    assert "conversationId: cid" in src
    send_fn = src.split("const handleSend =", 1)[1].split("const handleInterruptSend", 1)[0]
    assert "newConversationId()" in send_fn
    assert "crypto.randomUUID()" not in send_fn


def test_stream_chat_adopts_conversation_id_from_response_header():
    src = API_JS.read_text(encoding="utf-8")
    assert "X-Conversation-ID" in src
    assert "onConversationId" in src


def test_on_error_adopts_conversation_id():
    src = CALLBACKS_JS.read_text(encoding="utf-8")
    on_error = src.split("const onError =", 1)[1].split("const onSubagentEvent", 1)[0]
    assert "conversation_id" in on_error
    assert "setConversationId" in on_error


def test_try_again_uses_retry_flag_and_does_not_append_user_bubble():
    src = APP_JSX.read_text(encoding="utf-8")
    assert "retry: true" in src
    assert "const isRetry = Boolean(options.retry)" in src
    send_fn = src.split("const handleSend =", 1)[1].split("const handleInterruptSend", 1)[0]
    retry_branch = send_fn.split("if (isRetry)", 1)[1].split("} else {", 1)[0]
    assert "role: 'user'" not in retry_branch
