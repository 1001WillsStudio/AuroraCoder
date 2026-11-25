import requests
from fastapi.testclient import TestClient

from gateway import api
from src import gateway_access
from src.core_tools import memory_client


def test_memory_tool_uses_scoped_loopback_signature(monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "local fixture only")
    monkeypatch.setattr(api, "AUTH_PASSWORD", "local fixture only")
    client = TestClient(api.app, client=("127.0.0.1", 41000))
    captured = {}

    def route(session, method, url, *, headers, allow_redirects, **kwargs):
        assert not session.trust_env
        assert not allow_redirects
        captured.update(headers)
        response = client.request(method, url, headers=headers, params=kwargs.get("params"))
        result = requests.Response()
        result.status_code = response.status_code
        result._content = response.content
        return result

    monkeypatch.setattr(requests.Session, "request", route)
    assert memory_client.get_stance() == ""
    assert gateway_access.HEADER in captured
    assert client.get("/api/memory/stance", headers=captured).status_code == 200
    assert client.get("/api/memory/stance").status_code == 401
    assert client.get("/api/memory/recall", headers=captured).status_code == 401
    assert client.get("/api/settings", headers=captured).status_code == 401

    nonlocal_client = TestClient(api.app, client=("192.0.2.1", 41000))
    assert nonlocal_client.get("/api/memory/stance", headers=captured).status_code == 401


def test_service_auth_stays_local_and_supported_routes_only(monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "local fixture only")
    calls = []
    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: calls.append(kwargs))

    gateway_access.gateway_tool_request("POST", "https://example.invalid/api/chat")
    gateway_access.gateway_tool_request("POST", "http://localhost:8081/api/settings")
    assert calls == [{}, {}]
