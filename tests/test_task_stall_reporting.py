from types import SimpleNamespace

import pytest

from gateway import streaming
from gateway.conversation_store import ConversationStore
from src.task_checklist import TaskResume, TaskStart


def tool_result(name, call_id, content="Tool returned"):
    return [
        {"role": "assistant", "tool_calls": [{"id": call_id, "function": {"name": name}}]},
        {"role": "tool", "tool_call_id": call_id, "content": content},
    ]


@pytest.mark.parametrize("with_work", [False, True])
def test_rewording_checkpoint_only_does_not_restart_task_stall_counter(tmp_path, monkeypatch, with_work):
    store = ConversationStore(tmp_path)
    cid = store.create_conversation()
    task = store.tasks.start(cid, TaskStart(objective="Review the result", items=["Verify the result"]),
                             str(tmp_path), "Review the result", TaskResume())
    old_progress = task.progress.model_copy(update={"checkpoint": "Will keep checking."})
    updated = old_progress.model_copy(update={"checkpoint": "Checking is still in progress."})
    store.tasks.save(task, stalled=1, progress=updated)
    stream = streaming.ActiveStream(cid, status="completed", initial_task_progress=old_progress)
    monkeypatch.setattr(streaming, "store", store)
    monkeypatch.setattr(streaming, "start_task_segment", lambda *_args, **_kwargs: "next")
    monkeypatch.setattr(streaming, "_memory_ops_executor", SimpleNamespace(submit=lambda *_: None))
    current = [
        {"role": "user", "content": "Continue"},
        *(tool_result("read_file", "recent") if with_work else []),
        *tool_result("update_task_checklist", "rewrite"),
    ]
    raw = [*tool_result("read_file", "old"), *current]

    streaming._finish_task_segment(stream, raw, {"old"})

    assert stream.new_conversation_id == ("next" if with_work else None)
    assert store.tasks.get(cid).reason == ("" if with_work else "no_progress")


@pytest.mark.parametrize("result, turns", [
    ("Working Directory: /workspace\nExit Code: 1\n\nSTDERR:\nNo fixture", 2),
    ("Cannot connect to the test service", 3),
])
def test_repeated_result_and_checkpoint_rewrites_eventually_pause(tmp_path, monkeypatch, result, turns):
    store = ConversationStore(tmp_path)
    cid = store.create_conversation()
    store.tasks.start(cid, TaskStart(objective="Verify the fixture", items=["Verify the fixture"]),
                      str(tmp_path), "Verify the fixture", TaskResume())
    monkeypatch.setattr(streaming, "_memory_ops_executor", SimpleNamespace(submit=lambda *_: None))

    def continue_task(task, *, stalled, **_kwargs):
        store.tasks.save(task, stalled=stalled)
        return "next"

    monkeypatch.setattr(streaming, "start_task_segment", continue_task)
    for turn in range(turns):
        # Reload so changing the tool-call UUID and restarting the gateway cannot reset the count.
        store = ConversationStore(tmp_path)
        monkeypatch.setattr(streaming, "store", store)
        task = store.tasks.get(cid)
        progress = task.progress.model_copy(update={"checkpoint": f"Looking again, attempt {turn}."})
        store.tasks.save(task, progress=progress)
        stream = streaming.ActiveStream(cid, status="completed", initial_task_progress=task.progress)
        streaming._finish_task_segment(stream, [
            {"role": "user", "content": "Continue"},
            *tool_result("run_terminal_command", f"attempt-{turn}", result),
            *tool_result("update_task_checklist", f"rewrite-{turn}"),
            {"role": "assistant", "content": f"I am still working, attempt {turn}."},
        ], set())

    assert stream.new_conversation_id is None and stream.status == "interrupted"
    assert store.tasks.get(cid).reason == "no_progress"
