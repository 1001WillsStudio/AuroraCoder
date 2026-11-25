import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from gateway import routes, streaming
from gateway.api import app
from gateway.conversation_store import ConversationStore
from src.message_provenance import TURN_INPUT_RECEIPT
from src.task_checklist import TaskResume, TaskStart


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["context_limit_reached", "completed"])
async def test_enqueued_steering_survives_context_handoff(tmp_path, monkeypatch, terminal_status):
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


@pytest.mark.asyncio
@pytest.mark.parametrize("with_goal", [False, True])
async def test_model_boundary_inserts_accepted_followup_without_new_conversation(tmp_path, monkeypatch, with_goal):
    directory = tmp_path / "store"
    db = ConversationStore(directory)
    cid = db.create_conversation()
    stream = streaming.ActiveStream(cid, task_token="lease")
    if with_goal:
        task = db.tasks.start(cid, TaskStart(objective="Verify", items=["Check fixture"]),
                              str(tmp_path), "First request", TaskResume())
        stream.initial_task_progress = task.progress
    (tmp_path / "scope.txt").write_text("new scope", encoding="utf-8")
    monkeypatch.setattr(routes, "task_workspace", lambda: str(tmp_path))
    monkeypatch.setattr(routes, "store", db)
    monkeypatch.setattr(routes, "active_streams", {cid: stream})
    receipt = str(uuid4())
    request_body = {"receipt": receipt, "message": "Read this instead.", "attached_files": ["scope.txt"]}
    first_exchange = [
        {"role": "user", "content": "First request"},
        {"role": "assistant", "tool_calls": [{"id": "call", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call", "content": "Observed result"},
    ]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        admission = f"/api/conversations/{cid}/task/steering"
        boundary = f"/internal/turn-inputs/{cid}"
        headers = {"X-Task-Token": "lease"}
        assert (await client.post(admission, json=request_body)).status_code == 202
        db = ConversationStore(directory)
        monkeypatch.setattr(routes, "store", db)
        assert (await client.post(boundary, json={})).status_code == 409
        assert (await client.post(boundary, json={}, headers=headers)).json() == {"pending": True}
        result = (await client.post(boundary, json={"messages": first_exchange}, headers=headers)).json()
        assert len(result["inputs"]) == 1 and result["inputs"][0][TURN_INPUT_RECEIPT] == receipt
        assert "scope.txt" in result["inputs"][0]["content"]
        assert [message["role"] for message in db.get_messages(cid)] == ["user", "assistant", "tool", "user"]
        assert stream.new_conversation_id is None and not stream.cancel_event.is_set()
        assert (await client.post(admission, json=request_body)).json() == {"receipt": receipt}
        assert (await client.post(boundary, json={"finishing": True}, headers=headers)).json() == {"pending": False}
        assert (await client.post(admission, json={**request_body, "receipt": str(uuid4())})).status_code == 409
        if with_goal:
            task = db.tasks.get(cid)
            assert not task.pending_steering and result["task_state"]["steering_count"] == 1
        else:
            assert not db.pending_turn_inputs(cid) and db.has_turn_input(cid, receipt)
