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
@pytest.mark.parametrize("terminal_status", ["context_limit_reached", "completed"])
async def test_enqueued_steering_waits_for_turn_end_and_reaches_successor(tmp_path, monkeypatch, terminal_status):
    db = ConversationStore(tmp_path / "store")
    cid = db.create_conversation()
    task = db.tasks.start(cid, TaskStart(objective="Verify build", items=["Check output"]), str(tmp_path),
                          "Verify build", TaskResume())
    (tmp_path / "scope.txt").write_text("fresh scope", encoding="utf-8")
    old = streaming.ActiveStream(cid, status="running", task_token="old", initial_task_progress=task.progress)
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
        rejected = await client.post(path, json={
            "receipt": str(uuid4()), "message": "Do not save half of this.",
            "attached_files": ["scope.txt", "missing.txt"],
        })
        assert rejected.status_code == 400
        second = await client.post(path, json={"receipt": str(uuid4()), "message": "Keep the release files read-only."})
        assert second.status_code == 202
        db = ConversationStore(tmp_path / "store")
        monkeypatch.setattr(routes, "store", db)
        monkeypatch.setattr(streaming, "store", db)
        assert len(db.tasks.get(cid).pending_steering) == 2
        assert old.status == "running" and not old.cancel_event.is_set()
        assert old.new_conversation_id is None and seen == []
        headers = {"X-Task-Token": "old"}
        checkpoint = await client.post(f"/internal/task-checkpoint/{cid}", headers=headers, json={"checkpoint": "Running"})
        state = checkpoint.json()["task_state"]
        assert state["steering"] == "" and "steering_count" not in state
        for source in ({"source": "latest"}, {"source": "steering", "index": 0}):
            assert (await client.post(f"/internal/task-request/{cid}", headers=headers, json=source)).status_code == 404

        old.status = terminal_status
        final = [{"role": "user", "content": "Verify build"},
                 {"role": "assistant", "content": "The current turn concluded."}]
        streaming._finish_task_segment(old, final, set())
        await asyncio.wait_for(active[old.new_conversation_id].task, timeout=3)
        assert db.get_messages(cid) == final
        successor, body = seen[0]
        assert successor == old.new_conversation_id
        assert "scope.txt" in body["messages"][-1]["content"]
        assert "Use the new scope." in body["messages"][-1]["content"]
        assert body["message"] == "Keep the release files read-only."
        assert body["task_state"]["steering_count"] == 2
        assert not db.tasks.get(cid).pending_steering
        assert (await client.post(path, json=submission)).json() == first.json()
        assert len(db.tasks.get(cid).steering_history) == 2


@pytest.mark.asyncio
async def test_handoff_orders_pending_receipt_before_a_new_natural_resume(tmp_path, monkeypatch):
    db = ConversationStore(tmp_path / "store")
    cid = db.create_conversation()
    task = db.tasks.start(cid, TaskStart(objective="Verify build", items=["Check output"]), str(tmp_path),
                          "Verify build", TaskResume())
    monkeypatch.setattr(streaming, "store", db)
    monkeypatch.setattr(streaming, "WORKSPACE", tmp_path)
    monkeypatch.setattr(streaming, "active_streams", {})
    bodies = []

    async def backend(_stream, body):
        bodies.append(body)

    monkeypatch.setattr(streaming, "_proxy_backend_stream", backend)
    queued = db.tasks.queue_steering(task, "Check the original fixture first.", str(uuid4()))
    paused = db.tasks.save(queued, status="paused")
    reopened = db.tasks.reopen(paused, "Continue, and use the updated fixture.", TaskResume(), None)
    successor = streaming.start_task_segment(reopened, fresh=True)
    await streaming.active_streams[successor].task
    assert bodies[0]["messages"][-1]["content"] == "Check the original fixture first."
    assert bodies[0]["message"] == "Continue, and use the updated fixture."
    assert [entry.applied_to for entry in db.tasks.get(cid).steering_history] == [successor, successor]
