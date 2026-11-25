import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from gateway import routes, streaming
from gateway.api import app
from gateway.conversation_store import ConversationStore
from src.task_checklist import TaskResume, TaskStart


@pytest.mark.asyncio
async def test_enqueued_steering_survives_reload_and_is_applied_before_successor_dispatch(tmp_path, monkeypatch):
    db = ConversationStore(tmp_path / "store")
    cid = db.create_conversation()
    task = db.tasks.start(cid, TaskStart(objective="Verify build", items=["Check output"]), str(tmp_path),
                          "Verify build", TaskResume())
    (tmp_path / "scope.txt").write_text("fresh scope", encoding="utf-8")
    old = streaming.ActiveStream(cid, status="context_limit_reached", task_token="old", initial_task_progress=task.progress)
    active = {cid: old}
    monkeypatch.setattr(routes, "store", db)
    monkeypatch.setattr(routes, "active_streams", active)
    monkeypatch.setattr(streaming, "active_streams", active)
    monkeypatch.setattr(streaming, "WORKSPACE", tmp_path)
    monkeypatch.setattr(streaming, "_memory_ops_executor", SimpleNamespace(submit=lambda *_: None))
    seen = []

    async def backend(stream, body):
        seen.append((stream.conversation_id, body))

    monkeypatch.setattr(streaming, "_proxy_backend_stream", backend)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        path = f"/api/conversations/{cid}/task/steering"
        submission = {"receipt": str(uuid4()), "message": "Use the new scope.", "attached_files": ["scope.txt"]}
        first = await client.post(path, json=submission)
        assert first.status_code == 202 and first.json() == {"receipt": submission["receipt"]}
        again = await client.post(path, json=submission)
        assert again.json() == first.json()
        second = await client.post(path, json={"receipt": str(uuid4()), "message": "Keep the release files read-only."})
        assert second.status_code == 202
        db = ConversationStore(tmp_path / "store")
        monkeypatch.setattr(routes, "store", db)
        monkeypatch.setattr(streaming, "store", db)
        check = await client.post(f"/internal/task-boundary/{cid}", headers={"X-Task-Token": "old"})
        assert check.json() == {"pending": True}

        streaming._finish_task_segment(old, [], set())
        await asyncio.wait_for(active[old.new_conversation_id].task, timeout=3)
        successor, body = seen[0]
        assert successor == old.new_conversation_id
        assert "scope.txt" in body["messages"][-1]["content"]
        assert "Use the new scope." in body["messages"][-1]["content"]
        assert body["message"] == "Keep the release files read-only."
        assert not db.tasks.get(cid).pending_steering
        assert (await client.post(path, json=submission)).json() == first.json()
        assert len(db.tasks.get(cid).steering_history) == 2
