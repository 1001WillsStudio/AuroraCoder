from types import SimpleNamespace

from fastapi.testclient import TestClient

from gateway import api, routes
from gateway.conversation_store import ConversationStore


def test_gateway_restart_restores_a_retryable_turn_and_its_partial_history(tmp_path, monkeypatch):
    saved_before_stop = ConversationStore(tmp_path)
    cid = saved_before_stop.create_conversation()
    done_id = saved_before_stop.create_conversation()
    history = [
        {"role": "user", "content": "Keep this turn through the restart."},
        {"role": "assistant", "content": "Part of the answer."},
    ]
    saved_before_stop.save_messages(cid, history)
    saved_before_stop.save_frontend_messages(cid, history)
    saved_before_stop.update_status(cid, "running")
    saved_before_stop.update_status(done_id, "completed")

    reopened = ConversationStore(tmp_path)
    monkeypatch.setattr(api, "store", reopened)
    monkeypatch.setattr(routes, "store", reopened)
    monkeypatch.setattr(api, "sync_tool_env_vars", lambda: None)
    monkeypatch.setattr(api, "configure_github_auth", lambda: None)

    class OfflineBackend:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(api, "httpx", SimpleNamespace(AsyncClient=OfflineBackend))
    with TestClient(api.app) as client:
        conversation = client.get(f"/api/conversations/{cid}").json()
        replay = client.get(f"/api/conversations/{cid}/stream")

    assert conversation["status"] == "error"
    assert conversation["messages"] == history
    assert conversation["frontend_messages"][:-1] == history
    error = conversation["frontend_messages"][-1]
    assert error["canRetry"]
    assert "restarted" in error["content"]
    assert "restarted" in replay.text
    assert reopened.get_conversation(done_id)["status"] == "completed"
    assert ConversationStore(tmp_path).get_conversation(cid)["status"] == "error"
