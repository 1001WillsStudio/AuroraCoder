import pytest

from gateway import routes, streaming
from gateway.conversation_store import ConversationStore
from src.task_checklist import TaskCheckpointUpdate, TaskItem, TaskResume, TaskStart


@pytest.mark.asyncio
@pytest.mark.parametrize(("reason", "restored_status", "provider"), [
    ("error", "active", "recovery"),
    ("error", "active", None),
    ("paused", "historical", "recovery"),
])
async def test_retry_uses_saved_task_without_restarting_it(tmp_path, monkeypatch, reason, restored_status, provider):
    store = ConversationStore(tmp_path / "conversations")
    cid = store.create_conversation()
    workspace = str(tmp_path / "workspace")
    task = store.tasks.start(
        cid, TaskStart(objective="Review two files", items=["Inspect first", "Inspect second"]),
        workspace, "Review the files in this workspace", TaskResume(provider_id="original"),
    )
    task = store.tasks.checkpoint(task, TaskCheckpointUpdate(items=[
        TaskItem(text="Inspect first", evidence="First file passed"),
        TaskItem(text="Inspect second"),
    ], checkpoint="Read second file"))
    task = store.tasks.save(task, status="paused", reason=reason)
    history = [{"role": "user", "content": "Review the files in this workspace"}]
    store.save_messages(cid, history)
    captured = []
    active = {}

    class Request:
        async def json(self):
            return {"conversation_id": cid, "retry": True, "message": history[-1]["content"], "messages": history,
                    "max_iterations": 7, **({"provider_id": provider} if provider else {})}

    async def backend(stream, body):
        captured.append((stream, body))

    monkeypatch.setattr(routes, "store", store)
    monkeypatch.setattr(streaming, "active_streams", active)
    monkeypatch.setattr(routes, "active_streams", active)
    monkeypatch.setattr(routes, "task_workspace", lambda: workspace)
    monkeypatch.setattr(routes, "_get_workspace", lambda: tmp_path)
    monkeypatch.setattr(routes, "_proxy_backend_stream", backend)
    monkeypatch.setattr(streaming, "store", store)

    response = await routes.proxy_chat(Request())
    assert response.headers["x-conversation-id"] == cid
    await active[cid].task

    (stream, request), = captured
    assert request["message"] is None
    assert request["task_token"] == stream.task_token
    assert request["task_state"]["status"] == restored_status
    assert request["task_state"]["progress"]["items"][0]["evidence"] == "First file passed"
    assert request["task_state"]["progress"]["checkpoint"] == "Read second file"
    saved = store.tasks.get(cid)
    assert saved.id == task.id
    assert saved.conversation_ids == [cid]
    assert saved.request == task.request
    assert saved.progress == task.progress
    if reason == "error":
        assert stream.task_active
        assert saved.status == "active"
        assert saved.options.provider_id == (provider or "original")
        assert request["provider_id"] == saved.options.provider_id
    else:
        assert not stream.task_active
        assert saved.status == "paused"
