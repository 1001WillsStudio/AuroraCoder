import httpx
import pytest

from gateway import routes
from gateway.api import app
from gateway.conversation_store import ConversationStore
from gateway.streaming import ActiveStream
from src.task_checklist import AgentTaskTools, TaskResume, TaskStart


@pytest.mark.asyncio
async def test_latest_steering_middle_can_be_read_in_a_new_context_after_restart(tmp_path, monkeypatch):
    first_store = ConversationStore(tmp_path)
    cid = first_store.create_conversation()
    second = first_store.create_conversation()
    task = first_store.tasks.start(cid, TaskStart(objective="Review", items=["Verify"]),
                                   str(tmp_path), "Initial request", TaskResume())
    task = first_store.tasks.reopen(task, "Earlier note. " * 120, TaskResume(), None)
    marker = "Use the validated integration branch only."
    latest = "A" * 2850 + marker + "Z" * 2850
    task = first_store.tasks.reopen(task, latest, TaskResume(), None)
    first_store.tasks.save(task, conversation_ids=[cid, second])
    store = ConversationStore(tmp_path)
    task = store.tasks.get(second)
    assert marker not in task.prompt_state()["steering"]
    assert marker not in str(task.public())
    stream = ActiveStream(conversation_id=second, task_token="synthetic-lease",
                          initial_task_progress=task.progress)
    monkeypatch.setattr(routes, "store", store)
    monkeypatch.setattr(routes, "active_streams", {second: stream})
    agent = AgentTaskTools(second, stream.task_token, task.prompt_state())
    assert any(tool["function"]["name"] == "read_task_request" for tool in agent.schemas())
    assert "latest" in agent.prompt()
    store.tasks.reopen(task, "Continue.", TaskResume(), None)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        headers = {"X-Task-Token": stream.task_token}
        path = f"/internal/task-request/{second}"
        result = await client.post(path, headers=headers, json={
            "source": "steering", "index": 1, "offset": 2850, "max_chars": len(marker),
        })
        assert result.json()["text"] == marker
        assert (await client.post(path, headers=headers, json={"source": "latest"})).json()["text"] == "Continue."
        assert (await client.post(path, headers=headers, json={})).json()["text"] == "Initial request"


@pytest.mark.asyncio
async def test_current_task_stream_can_read_a_detail_omitted_by_compaction(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    cid = store.create_conversation()
    original = "A" * 9000 + "Use the validated feature tree. " + "Z" * 9000
    task = store.tasks.start(cid, TaskStart(objective="Review the feature", items=["Review tree"]),
                             str(tmp_path), original, TaskResume())
    stream = ActiveStream(conversation_id=cid, task_token="synthetic-lease",
                          initial_task_progress=task.progress)
    monkeypatch.setattr(routes, "store", store)
    monkeypatch.setattr(routes, "active_streams", {cid: stream})

    state = task.prompt_state()
    assert "validated feature tree" not in state["request"]
    agent = AgentTaskTools(cid, stream.task_token, state)
    assert any(tool["function"]["name"] == "read_task_request" for tool in agent.schemas())
    assert "read_task_request" in agent.prompt()
    assert not any(tool["function"]["name"] == "read_task_request"
                   for tool in AgentTaskTools(cid, "unused", {"status": "active"}).schemas())

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        path = f"/internal/task-request/{cid}"
        body = {"offset": 9000, "max_chars": 30}
        assert (await client.post(path, json=body)).status_code == 409
        headers = {"X-Task-Token": stream.task_token}
        found = await client.post(path, headers=headers, json=body)
        assert found.json() == {"text": original[9000:9030], "offset": 9000,
                                "total_chars": len(original), "next_offset": 9030}
        assert (await client.post(path, headers=headers, json={"offset": -1})).status_code == 422
        stream.cancel_event.set()
        assert (await client.post(path, headers=headers, json=body)).status_code == 409


def test_task_source_tool_keeps_the_current_checklist_state(monkeypatch):
    state = {"status": "active", "request_length": 10000, "objective": "Review"}
    agent = AgentTaskTools("for-fixture", "synthetic-lease", state)

    def post(url, **kwargs):
        assert url.endswith("/internal/task-request/for-fixture")
        assert kwargs["headers"] == {"X-Task-Token": "synthetic-lease"}
        assert kwargs["json"] == {"offset": 5000, "max_chars": 24}
        return httpx.Response(200, json={"text": "Read this first", "offset": 5000,
                                         "total_chars": 10000, "next_offset": 5015})

    monkeypatch.setattr(httpx, "post", post)
    assert "Read from offset 5015" in agent.call("read_task_request", {"offset": 5000, "max_chars": 24})
    assert agent.state is state
