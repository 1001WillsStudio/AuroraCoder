import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from gateway import streaming
from gateway.conversation_store import ConversationStore
from src.backend_access import backend_auth_headers

backend = importlib.import_module("src.web_api.app")


def test_configured_password_protects_direct_execution_and_reload(monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "密码-only-local-fixture")
    calls = []
    monkeypatch.setattr(backend.provider_manager, "reload", lambda: calls.append("reload"))

    async def chat(*_args, **_options):
        calls.append("chat")
        yield "event: done\ndata: {}\n\n"

    monkeypatch.setattr(backend, "stream_chat_response", chat)
    client = TestClient(backend.app)
    assert client.post("/api/chat", json={"message": "Fixture"}).status_code == 401
    assert client.post("/api/reload").status_code == 401
    assert client.get("/api/health").status_code == 200
    assert calls == []

    headers = backend_auth_headers()
    assert "密码" not in str(headers)
    assert client.post("/api/reload", headers=headers).status_code == 200
    answer = client.post("/api/chat", headers=headers, json={"message": "Fixture"})
    assert answer.status_code == 200
    assert calls == ["reload", "chat"]


@pytest.mark.asyncio
async def test_gateway_supplies_private_backend_auth_for_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "local fixture")
    store = ConversationStore(tmp_path)
    monkeypatch.setattr(streaming, "store", store)
    monkeypatch.setattr(streaming, "_schedule_memory_distillation", lambda *_: None)
    seen = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        @asynccontextmanager
        async def stream(self, *_, headers, **_kwargs):
            seen.append(headers)

            async def chunks():
                yield "event: done\ndata: {\"status\":\"completed\"}\n\n"

            yield SimpleNamespace(status_code=200, aiter_text=chunks)

    monkeypatch.setattr(streaming, "httpx", SimpleNamespace(
        AsyncClient=lambda **_: Client(), Timeout=lambda *_: None, ConnectError=ConnectionError,
    ))
    cid = store.create_conversation()
    await streaming._proxy_backend_stream(streaming.ActiveStream(conversation_id=cid), {})
    assert seen == [{"Content-Type": "application/json", **backend_auth_headers()}]
