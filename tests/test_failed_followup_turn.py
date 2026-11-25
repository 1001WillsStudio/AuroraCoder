import copy
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient

from gateway import routes, streaming
from gateway.api import app
from gateway.conversation_store import ConversationStore, messages_for_retry
from gateway.task_instruction_display import user_message_for_frontend


def test_followup_is_retryable_when_backend_fails_before_its_first_event(tmp_path, monkeypatch):
    history = ConversationStore(tmp_path / "history")
    cid = history.create_conversation()
    earlier = [
        {"role": "user", "content": "Original request"},
        {"role": "assistant", "content": "Original answer"},
    ]
    latest = "Use the new file for this follow-up."
    history.save_messages(cid, copy.deepcopy(earlier))
    history.save_frontend_messages(cid, copy.deepcopy(earlier))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "instructions.txt").write_text("Use these instructions")

    observed = []

    class UnavailableBackend:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        @asynccontextmanager
        async def stream(self, *_, json, **_options):
            observed.append(copy.deepcopy(json))

            async def error_body():
                return b"Model unavailable"

            yield SimpleNamespace(status_code=503, aread=error_body)

    monkeypatch.setattr(routes, "store", history)
    monkeypatch.setattr(streaming, "store", history)
    monkeypatch.setattr(routes, "_get_workspace", lambda: workspace)
    monkeypatch.setattr(streaming, "_schedule_memory_distillation", lambda *_: None)
    monkeypatch.setattr(streaming, "httpx", SimpleNamespace(
        AsyncClient=lambda **_: UnavailableBackend(), Timeout=lambda *_: None,
        ConnectError=ConnectionError,
    ))
    client = TestClient(app)

    response = client.post("/api/chat", json={
        "conversation_id": cid,
        "message": latest,
        "messages": earlier,
        "attached_files": ["instructions.txt"],
    })
    assert response.status_code == 200

    reloaded = client.get(f"/api/conversations/{cid}").json()
    assert reloaded["status"] == "error"
    raw_user_entries = [m for m in reloaded["messages"] if m["role"] == "user"]
    assert [m["content"] for m in raw_user_entries] == ["Original request", observed[0]["message"]]
    assert "instructions.txt" in raw_user_entries[-1]["content"]

    # A client may retry with an older transcript it cached before the failure.
    response = client.post("/api/chat", json={
        "conversation_id": cid, "retry": True, "message": latest, "messages": earlier,
    })
    assert response.status_code == 200
    assert observed[1]["message"] is None
    assert [m["content"] for m in observed[1]["messages"] if m["role"] == "user"] == [
        "Original request", observed[0]["message"],
    ]
    reloaded = client.get(f"/api/conversations/{cid}").json()
    assert [m["content"] for m in reloaded["frontend_messages"] if m["role"] == "user"] == [
        "Original request", latest,
    ]


def test_retry_recovers_a_legacy_failed_turn_with_the_same_text_and_context():
    original = [{"role": "user", "content": "Check it"}, {"role": "assistant", "content": "Checked"}]
    pending = {
        "role": "user", "content": "Check it", "taskInstruction": "Keep the current branch",
        "attachedFiles": ["README.md"], "ts": "2026-10-03T09:00:00+00:00",
    }
    visible = [*original, pending, {"role": "assistant", "isError": True, "content": "Model error"}]

    restored = messages_for_retry(original, "Unrelated cached text", frontend_messages=visible)

    assert restored[:-1] == original
    assert restored[-1]["ts"] == pending["ts"]
    assert user_message_for_frontend(restored[-1]["content"]) == {
        key: value for key, value in pending.items() if key != "ts"
    }
    assert messages_for_retry(restored, frontend_messages=visible) == restored
