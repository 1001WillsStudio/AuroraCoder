import pytest

from gateway import routes
from gateway.conversation_store import ConversationStore
from src.task_checklist import TaskResume, TaskStart


@pytest.mark.asyncio
async def test_a_long_task_keeps_user_scope_at_both_ends_after_context_reload(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    active = {}
    monkeypatch.setattr(routes, "store", store)
    monkeypatch.setattr(routes, "active_streams", active)
    monkeypatch.setattr(routes, "_get_workspace", lambda: tmp_path)
    start = "Audit the test worktree and preserve its results."
    end = "Only inspect the staging checkout. Do not publish changes."
    original = f"{start}\n{'Local diagnostic fixture. ' * 1000}\n{end}"

    class Request:
        async def json(self):
            return {"message": original, "max_iterations": 8}

    async def backend(*_args):
        return None

    monkeypatch.setattr(routes, "_proxy_backend_stream", backend)
    response = await routes.proxy_chat(Request())
    cid = response.headers["x-conversation-id"]
    stream = active[cid]
    await stream.task
    task = store.tasks.start(cid, TaskStart(objective="Audit the checkout", items=["Inspect worktree"]),
                             str(tmp_path), stream.task_request, TaskResume())

    reloaded = ConversationStore(tmp_path).tasks.get(cid)
    assert reloaded.request == task.request == original
    excerpt = reloaded.prompt_state()["request"]
    assert len(excerpt) <= 6000
    assert excerpt.startswith(start) and excerpt.endswith(end)
    assert "omitted" in excerpt


def test_long_latest_steering_keeps_both_ends(tmp_path):
    store = ConversationStore(tmp_path)
    cid = store.create_conversation()
    task = store.tasks.start(cid, TaskStart(objective="Review the worktree", items=["Inspect worktree"]),
                             str(tmp_path), "Review the worktree", TaskResume())
    task = store.tasks.save(task, steering="Earlier constraint: keep the original patch.")
    latest = "Now inspect the feature directory. " + "Diagnostic fixture. " * 900 + "Keep output in the workspace."
    task = store.tasks.reopen(task, latest, TaskResume(), None)
    steering = task.prompt_state()["steering"]
    assert "Earlier constraint" in steering
    assert "Now inspect the feature directory" in steering
    assert steering.endswith("Keep output in the workspace.")
    assert len(steering) <= 6000
