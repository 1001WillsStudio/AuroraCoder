import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from frontend import server


async def _stay_connected():
    await asyncio.Future()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/health", "/api/chat", "/api/conversations/abc/stream"])
async def test_desktop_bounds_time_before_chat_or_health_response_headers(monkeypatch, path):
    closed = []

    async def send(*_args, **_kwargs):
        await asyncio.sleep(0.05)
        raise AssertionError("A connected gateway never sent response headers")

    async def close():
        closed.append(True)

    monkeypatch.setattr(server, "_INTERACTIVE_HEADER_TIMEOUT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(server.httpx, "AsyncClient", lambda **_: SimpleNamespace(
        build_request=lambda *_args, **_kwargs: None, send=send, aclose=close,
    ))

    async def body():
        return b""

    request = SimpleNamespace(url=SimpleNamespace(query=""), method="GET", headers={}, body=body,
                              receive=_stay_connected)
    response = await server._proxy(request, path)
    assert response.status_code == 504
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/chat", "/api/toolstore/refresh"])
async def test_desktop_leaves_body_stream_and_non_chat_request_unbounded(monkeypatch, path):
    async def chunks():
        await asyncio.sleep(0.04)
        yield b"completed"

    async def close():
        pass

    async def send(*_args, **_kwargs):
        if path.endswith("/refresh"):
            await asyncio.sleep(0.04)
        return SimpleNamespace(status_code=200, headers={}, aiter_bytes=chunks, aclose=close)

    monkeypatch.setattr(server, "_INTERACTIVE_HEADER_TIMEOUT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(server.httpx, "AsyncClient", lambda **_: SimpleNamespace(
        build_request=lambda *_args, **_kwargs: None, send=send, aclose=close,
    ))

    async def body():
        return b""

    request = SimpleNamespace(url=SimpleNamespace(query=""), method="POST", headers={}, body=body,
                              receive=_stay_connected)
    response = await server._proxy(request, path)
    assert response.status_code == 200
    assert [chunk async for chunk in response.body_iterator] == [b"completed"]


@pytest.mark.asyncio
async def test_desktop_disconnect_cancels_chat_while_waiting_for_gateway_headers(monkeypatch):
    sending, disconnected = asyncio.Event(), asyncio.Event()
    released = []

    async def send(*_args, **_kwargs):
        sending.set()
        try:
            await asyncio.Future()
        finally:
            released.append("request")

    async def close():
        released.append("client")

    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def body():
        return b"{}"

    monkeypatch.setattr(server.httpx, "AsyncClient", lambda **_: SimpleNamespace(
        build_request=lambda *_args, **_kwargs: None, send=send, aclose=close,
    ))
    request = SimpleNamespace(url=SimpleNamespace(query=""), method="POST", headers={}, body=body,
                              receive=receive)
    pending = asyncio.create_task(server._proxy(request, "/api/chat"))
    await asyncio.wait_for(sending.wait(), 1)
    disconnected.set()
    response = await asyncio.wait_for(pending, 1)
    assert response.status_code == 499
    assert released == ["request", "client"]


@pytest.mark.parametrize(("error_type", "expected"), [(httpx.ConnectError, 502), (httpx.ConnectTimeout, 504)])
@pytest.mark.parametrize(("method", "path"), [("GET", "/health"), ("POST", "/api/chat")])
def test_desktop_gateway_outage_releases_client_and_returns_actionable_error(
    monkeypatch, error_type, expected, method, path,
):
    closed = []

    class NoGateway:
        def __init__(self, *, timeout, trust_env):
            assert not trust_env
            self.timeout = timeout

        def build_request(self, *_args, **_kwargs):
            return None

        async def send(self, _request, *, stream):
            assert stream
            raise error_type("Local fixture: no gateway")

        async def aclose(self):
            closed.append(self.timeout)

    monkeypatch.setattr(server.httpx, "AsyncClient", NoGateway)
    response = TestClient(server.app, raise_server_exceptions=False).request(method, path)
    assert response.status_code == expected
    assert "gateway" in response.json()["detail"].lower()
    timeout, = closed
    assert timeout.connect is not None and timeout.read is None


@pytest.mark.asyncio
async def test_desktop_releases_both_stream_resources_even_when_upstream_close_fails(monkeypatch):
    closed = []

    async def chunks():
        yield b": keepalive\n\n"

    async def close_response():
        closed.append("response")
        raise OSError("Synthetic close failure")

    async def close_client():
        closed.append("client")

    async def send(*_args, **_kwargs):
        return SimpleNamespace(status_code=200, headers={}, aiter_bytes=chunks, aclose=close_response)

    monkeypatch.setattr(server.httpx, "AsyncClient", lambda **_: SimpleNamespace(
        build_request=lambda *_args, **_kwargs: None, send=send, aclose=close_client,
    ))

    async def body():
        return b""

    request = SimpleNamespace(url=SimpleNamespace(query=""), method="GET", headers={}, body=body)
    response = await server._proxy(request, "/health")
    assert await anext(response.body_iterator) == b": keepalive\n\n"
    with pytest.raises(OSError, match="Synthetic close failure"):
        await response.body_iterator.aclose()
    assert closed == ["response", "client"]
