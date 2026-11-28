"""Lifecycle of the per-turn conversation-history credential.

The credential is the gateway proxy's authorization for the three private
read-only history routes, so its life is the proxy turn's life:

  * it is issued only after a turn is admitted (a 409 or a startup failure
    must not mint one);
  * standalone turns and automatic continuations both get it, because both
    are top-level user chats;
  * it is valid for as long as the turn runs — a multi-hour task is never cut
    off by a wall-clock deadline — and it is revoked on completion, cancel and
    error;
  * the backend bridge never forwards it to another host (no redirects) and
    never calls anything but the three private read endpoints.

These tests drive the real gateway app and the real streaming proxy against a
synthetic backend, so the admission, streaming and revocation paths are the
production ones.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from gateway import conversation_history as history
from gateway.conversation_store import ConversationStore
from memory.ops import conversation_search
from src.core_tools import conversation_history as tools_client

REAL_ASYNC_CLIENT = httpx.AsyncClient


# ---------------------------------------------------------------------------
# Fixtures: one isolated conversation store shared by every gateway module
# ---------------------------------------------------------------------------

@pytest.fixture
def gateway(tmp_path, monkeypatch):
    """Gateway in no-password mode, backed by a throwaway conversation store."""
    import gateway.api as api
    import gateway.routes as routes
    from gateway import streaming

    store = ConversationStore(tmp_path / "conversations")
    for module in (history, routes, streaming, api):
        monkeypatch.setattr(module, "store", store)
    monkeypatch.setattr(conversation_search, "store", store)

    # No ACCESS_PASSWORD: the ordinary no-password path must keep working.
    monkeypatch.setattr(api, "AUTH_PASSWORD", "")
    # Session-end memory work is out of scope here and would spawn threads.
    monkeypatch.setattr(streaming, "_schedule_memory_distillation", lambda *a, **k: None)
    monkeypatch.setattr(
        streaming, "_memory_ops_executor", SimpleNamespace(submit=lambda *a, **k: None)
    )

    history.clear_turn_credentials()
    streaming.active_streams.clear()
    try:
        yield SimpleNamespace(api=api, routes=routes, streaming=streaming, store=store)
    finally:
        history.clear_turn_credentials()
        streaming.active_streams.clear()


# ---------------------------------------------------------------------------
# Synthetic backend speaking the gateway's SSE contract
# ---------------------------------------------------------------------------

def _sse(event_type: str, data: dict) -> bytes:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode()


def _completed(raw_messages: list, provider: str = "fixture") -> bytes:
    return _sse("messages", {
        "status": "completed",
        "provider": provider,
        "committed": True,
        "messages": raw_messages,
        "raw_messages": raw_messages,
    })


class FakeBackend:
    """Mock agent backend; records every request body it receives."""

    def __init__(self, responder):
        self.requests: list[dict] = []
        self._responder = responder

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/api/chat"):
            return httpx.Response(200, json={"ok": True})
        body = json.loads(request.content or b"{}")
        self.requests.append(body)
        return await self._responder(body)

    def install(self, monkeypatch, streaming_module) -> None:
        transport = httpx.MockTransport(self._handle)

        def client_factory(**kwargs):
            return REAL_ASYNC_CLIENT(transport=transport, timeout=None)

        monkeypatch.setattr(streaming_module, "httpx", SimpleNamespace(
            AsyncClient=client_factory,
            Timeout=httpx.Timeout,
            ConnectError=httpx.ConnectError,
            HTTPError=httpx.HTTPError,
            HTTPStatusError=httpx.HTTPStatusError,
        ))


async def _probe(gateway, token, path="/api/conversation-history/list", params=None):
    """Call the gateway's history bridge exactly like the backend tool does."""
    headers = {history.TURN_TOKEN_HEADER: token} if token else {}
    async with REAL_ASYNC_CLIENT(
        transport=httpx.ASGITransport(app=gateway.api.app),
        base_url="http://gateway",
        timeout=None,
    ) as client:
        return await client.get(path, params=params or {}, headers=headers)


async def _run_chat(gateway, payload: dict) -> httpx.Response:
    """Send one chat through the real gateway route and drain its SSE stream."""
    async with REAL_ASYNC_CLIENT(
        transport=httpx.ASGITransport(app=gateway.api.app),
        base_url="http://gateway",
        timeout=None,
    ) as client:
        return await client.post("/api/chat", json=payload)


async def _wait_for_idle(streaming_module, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not streaming_module.active_streams:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"streams still active: {list(streaming_module.active_streams)}")


# ---------------------------------------------------------------------------
# Lifetime: the credential is owned by the turn, not by a clock
# ---------------------------------------------------------------------------

def test_active_turn_credential_never_ages_out(monkeypatch):
    """A long-running task keeps its credential no matter how old it is."""
    clock = [1_000_000.0]
    monkeypatch.setattr(history, "_now", lambda: clock[0])

    token = history.begin_turn_credential("chat-long")
    clock[0] += history.TURN_TOKEN_TTL_SECONDS * 3

    credential = history.resolve_turn_credential(token)
    assert credential is not None and credential.conversation_id == "chat-long"

    history.revoke_turn_credential(token)
    assert history.resolve_turn_credential(token) is None


def test_standalone_credential_keeps_its_fixed_ttl(monkeypatch):
    """Credentials minted outside the proxy loop still expire on their own."""
    clock = [1_000_000.0]
    monkeypatch.setattr(history, "_now", lambda: clock[0])

    token = history.issue_turn_credential("chat-fixture", ttl=10)
    assert history.resolve_turn_credential(token) is not None

    clock[0] += 11
    assert history.resolve_turn_credential(token) is None
    # Reaping is not just a read-side decision — the entry is dropped too.
    assert token not in history._credentials


# ---------------------------------------------------------------------------
# Admission: no credential on a refused or failed admission
# ---------------------------------------------------------------------------

def _count_minted(monkeypatch, streaming_module) -> list:
    minted: list = []
    real_begin = streaming_module.begin_turn_credential

    def spy(conversation_id, **kwargs):
        token = real_begin(conversation_id, **kwargs)
        minted.append(token)
        return token

    monkeypatch.setattr(streaming_module, "begin_turn_credential", spy)
    return minted




async def test_startup_failure_releases_claim_and_credential(gateway, monkeypatch):
    """A turn that cannot start must not keep its credential or a claim."""
    minted = _count_minted(monkeypatch, gateway.streaming)

    def broken_proxy(stream, body):
        raise RuntimeError("backend task could not be created")

    monkeypatch.setattr(gateway.routes, "_proxy_backend_stream", broken_proxy)

    async with REAL_ASYNC_CLIENT(
        transport=httpx.ASGITransport(app=gateway.api.app, raise_app_exceptions=False),
        base_url="http://gateway",
        timeout=None,
    ) as client:
        response = await client.post(
            "/api/chat", json={"conversation_id": "chat-broken", "message": "hello"},
        )

    assert response.status_code == 500
    assert len(minted) == 1
    assert history.resolve_turn_credential(minted[0]) is None
    # The conversation is free again, so the user can retry.
    assert "chat-broken" not in gateway.streaming.active_streams


# ---------------------------------------------------------------------------
# Real gateway streaming: a standalone turn
# ---------------------------------------------------------------------------

async def test_turn_credential_is_live_only_while_the_turn_runs(gateway, monkeypatch):
    store = gateway.store
    store.create_conversation(conversation_id="chat-past")
    store.save_messages("chat-past", [{"role": "user", "content": "deploy on Fridays"}])

    seen: dict = {}

    async def responder(body):
        token = body.get("conversation_history_token")
        seen["token"] = token
        raw = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

        async def events():
            # The private routes are not public, even with no ACCESS_PASSWORD.
            seen["anonymous"] = (await _probe(gateway, None)).status_code
            yield _sse("messages", {"status": "running", "messages": raw})
            # Mid-turn the bridge answers with the live credential.
            during = await _probe(gateway, token)
            seen["during"] = during.status_code
            seen["during_ids"] = [
                c["conversation_id"] for c in during.json().get("conversations", [])
            ]
            yield _completed(raw)

        return httpx.Response(
            200, content=events(), headers={"content-type": "text/event-stream"},
        )

    FakeBackend(responder).install(monkeypatch, gateway.streaming)

    response = await _run_chat(gateway, {"conversation_id": "chat-live", "message": "hello"})

    assert response.status_code == 200
    assert '"status": "completed"' in response.text
    assert seen["token"]
    assert seen["anonymous"] == 401
    assert seen["during"] == 200
    assert "chat-past" in seen["during_ids"]
    assert "chat-live" not in seen["during_ids"]  # never reads its own chat

    # Completion ends the turn: same token, now refused.
    assert history.resolve_turn_credential(seen["token"]) is None
    assert (await _probe(gateway, seen["token"])).status_code == 401
    assert "chat-live" not in gateway.streaming.active_streams


async def test_error_turn_revokes_credential(gateway, monkeypatch):
    store = gateway.store
    store.create_conversation(conversation_id="chat-error")
    seen: dict = {}

    async def responder(body):
        seen["token"] = body.get("conversation_history_token")
        return httpx.Response(500, content=b"backend exploded")

    FakeBackend(responder).install(monkeypatch, gateway.streaming)

    response = await _run_chat(gateway, {"conversation_id": "chat-error", "message": "hello"})

    assert response.status_code == 200  # the gateway relays the error over SSE
    assert seen["token"]
    assert history.resolve_turn_credential(seen["token"]) is None
    assert (await _probe(gateway, seen["token"])).status_code == 401
    assert store.get_conversation("chat-error")["status"] == "error"


async def test_cancel_revokes_credential_mid_turn(gateway, monkeypatch):
    store = gateway.store
    store.create_conversation(conversation_id="chat-cancel")

    stream = gateway.streaming.ActiveStream("chat-cancel")
    gateway.streaming.active_streams[stream.conversation_id] = stream
    body = {"conversation_id": "chat-cancel"}

    started = asyncio.Event()

    async def responder(_body):
        async def events():
            started.set()
            await asyncio.sleep(30)  # never finishes on its own
            yield _completed([{"role": "user", "content": "hello"}])

        return httpx.Response(
            200, content=events(), headers={"content-type": "text/event-stream"},
        )

    FakeBackend(responder).install(monkeypatch, gateway.streaming)
    gateway.streaming.launch_backend_stream(stream, body)
    token = stream.turn_token
    assert history.resolve_turn_credential(token) is not None
    await asyncio.wait_for(started.wait(), 2)

    await asyncio.wait_for(gateway.streaming._cancel_active_stream("chat-cancel"), 5)

    assert history.resolve_turn_credential(token) is None
    assert (await _probe(gateway, token)).status_code == 401
    assert "chat-cancel" not in gateway.streaming.active_streams


# ---------------------------------------------------------------------------
# Automatic continuation: same admission, same authorization
# ---------------------------------------------------------------------------

async def test_automatic_continuation_reads_history_with_its_own_credential(
    gateway, monkeypatch,
):
    store = gateway.store
    store.create_conversation(conversation_id="chat-past")
    store.save_messages("chat-past", [{"role": "user", "content": "deploy on Fridays"}])

    seen: dict = {"continuations": []}

    async def responder(body):
        if body["conversation_id"] == "chat-origin":
            seen["origin_token"] = body.get("conversation_history_token")
            raw = [
                {"role": "user", "content": "hand off"},
                {"role": "assistant", "content": "", "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "continue_as_new_chat",
                        "arguments": json.dumps({"prompt": "keep going with the task"}),
                    },
                }]},
            ]
            raw.append({"role": "tool", "tool_call_id": "c1", "content": gateway.streaming.CONTINUATION_TOOL_RESULT})
            return httpx.Response(200, content=_completed(raw))

        entry = {
            "conversation_id": body["conversation_id"],
            "token": body.get("conversation_history_token"),
            "message": body.get("message"),
        }
        seen["continuations"].append(entry)
        raw = [
            {"role": "user", "content": body.get("message") or ""},
            {"role": "assistant", "content": "continuing"},
        ]

        async def events():
            during = await _probe(gateway, entry["token"])
            entry["during"] = during.status_code
            entry["ids"] = [
                c["conversation_id"] for c in during.json().get("conversations", [])
            ]
            yield _completed(raw)

        return httpx.Response(
            200, content=events(), headers={"content-type": "text/event-stream"},
        )

    FakeBackend(responder).install(monkeypatch, gateway.streaming)

    response = await _run_chat(gateway, {"conversation_id": "chat-origin", "message": "start"})
    assert response.status_code == 200
    await _wait_for_idle(gateway.streaming)

    assert len(seen["continuations"]) == 1
    continuation = seen["continuations"][0]
    assert continuation["token"] and continuation["token"] != seen["origin_token"]
    # The automatic New Convo can read history while it runs, exactly like a
    # hand-typed turn.
    assert continuation["during"] == 200
    assert "chat-past" in continuation["ids"]
    assert continuation["conversation_id"] not in continuation["ids"]

    # And it is a top-level user chat, not a private subagent transcript.
    conversation = store.get_conversation(continuation["conversation_id"])
    assert conversation["type"] == "user_chat"
    assert conversation["parent_id"] is None

    # Both credentials are gone once their turns are over.
    assert history.resolve_turn_credential(continuation["token"]) is None
    assert history.resolve_turn_credential(seen["origin_token"]) is None
    assert (await _probe(gateway, continuation["token"])).status_code == 401




# ---------------------------------------------------------------------------
# Backend bridge: exact read endpoints, no credential on redirects
# ---------------------------------------------------------------------------

def test_bridge_refuses_paths_outside_the_read_endpoints(monkeypatch):
    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the bridge must not call non-read routes")

    monkeypatch.setattr(tools_client.requests, "get", explode)
    token = tools_client.set_turn_token("turn-token")
    try:
        data, error = tools_client._gateway_get("/api/conversations", {})
    finally:
        tools_client.reset_turn_token(token)

    assert data is None
    assert "unsupported route" in error


def test_bridge_does_not_forward_the_credential_across_a_redirect(monkeypatch):
    calls = []

    class Redirect:
        status_code = 302
        text = ""

        def json(self):  # pragma: no cover - a redirect is never parsed
            return {}

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return Redirect()

    monkeypatch.setattr(tools_client.requests, "get", fake_get)
    token = tools_client.set_turn_token("turn-token")
    try:
        text, _ = tools_client.list_conversations_tool({})
    finally:
        tools_client.reset_turn_token(token)

    assert calls and calls[0][1]["allow_redirects"] is False
    assert calls[0][0].endswith("/api/conversation-history/list")
    assert "redirected" in text


def test_bridge_reports_a_revoked_credential(monkeypatch):
    class Unauthorized:
        status_code = 401
        text = ""

        def json(self):  # pragma: no cover
            return {}

    monkeypatch.setattr(tools_client.requests, "get", lambda *a, **k: Unauthorized())
    token = tools_client.set_turn_token("stale-token")
    try:
        text, _ = tools_client.search_conversations_tool({"query": "deploy"})
    finally:
        tools_client.reset_turn_token(token)

    assert "revoked or has expired" in text
