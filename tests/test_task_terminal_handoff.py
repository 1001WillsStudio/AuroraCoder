from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from gateway import streaming
from gateway.conversation_store import ConversationStore
from gateway.task_terminal_jobs import pending_terminal_jobs
from src.task_checklist import TaskResume, TaskStart


@pytest.mark.asyncio
async def test_automatic_handoff_keeps_a_started_job_before_the_agent_can_checkpoint_it(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    cid = store.create_conversation()
    task = store.tasks.start(cid, TaskStart(objective="Verify background work", items=["Check its result"]),
                             str(tmp_path), "Verify background work", TaskResume())
    stream = streaming.ActiveStream(cid, initial_task_progress=task.progress)
    job_id = "a" * 32
    raw = [
        {"role": "user", "content": "Verify background work"},
        {"role": "assistant", "tool_calls": [{"id": "run", "function": {"name": "run_terminal_command"}}]},
        {"role": "tool", "tool_call_id": "run", "content": f"Status: running\nBackground PID: 7\nJob ID: {job_id}"},
    ]
    seen = []

    class Backend:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        @asynccontextmanager
        async def stream(self, *_args, **_kwargs):
            async def stream_body():
                for kind, status in [("messages", "running"), ("done", "context_limit_reached")]:
                    yield streaming._format_sse(kind, {"status": status, "raw_messages": raw, "messages": []})

            yield SimpleNamespace(status_code=200, aiter_text=stream_body)

    def next_segment(current, **_options):
        seen.append(current.prompt_state())
        return "next-segment"

    monkeypatch.setattr(streaming, "store", store)
    monkeypatch.setattr(streaming, "active_streams", {cid: stream})
    monkeypatch.setattr(streaming, "start_task_segment", next_segment)
    monkeypatch.setattr(streaming, "_memory_ops_executor", SimpleNamespace(submit=lambda *_: None))
    monkeypatch.setattr(streaming, "httpx", SimpleNamespace(
        AsyncClient=lambda **_: Backend(), Timeout=httpx.Timeout, ConnectError=httpx.ConnectError,
    ))
    await streaming._proxy_backend_stream(stream, {"conversation_id": cid})
    assert seen[0]["terminal_jobs"] == [job_id]
    assert ConversationStore(tmp_path).tasks.get(cid).terminal_jobs == [job_id]


def test_terminal_job_result_clears_only_the_matching_saved_job():
    existing = ["a" * 32, "b" * 32]
    raw = [
        {"role": "assistant", "tool_calls": [{"id": "poll", "function": {"name": "terminal_job_status"}}]},
        {"role": "tool", "tool_call_id": "poll",
         "content": f"Status: completed\nExit Code: 0\nJob ID: {existing[0]}"},
        {"role": "tool", "tool_call_id": "unrecognized",
         "content": f"Status: completed\nJob ID: {existing[1]}"},
    ]
    assert pending_terminal_jobs(existing, raw) == [existing[1]]
