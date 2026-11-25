import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from gateway import streaming
from gateway.conversation_store import ConversationStore


class Connected:
    async def is_disconnected(self):
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["done", "error"])
async def test_subscriber_gets_terminal_event_when_proxy_finished_before_the_iterator_started(event_type):
    stream = streaming.ActiveStream(conversation_id="test", finished=True)
    queue = asyncio.Queue()
    queue.put_nowait((event_type, {"message": "Terminal event"}))
    queue.put_nowait(None)

    events = [event async for event in streaming._subscriber_sse(stream, queue, Connected())]

    assert len(events) == 1
    assert events[0].startswith(f"event: {event_type}\n")


@pytest.mark.asyncio
async def test_late_resume_replays_the_original_terminal_event_type():
    stream = streaming.ActiveStream(
        conversation_id="test", finished=True, latest_event_type="done",
        latest_event_data={"status": "completed", "messages": []},
    )

    events = [event async for event in streaming._subscriber_sse(
        stream, asyncio.Queue(), Connected(), replay_latest=True,
    )]

    assert len(events) == 1
    assert events[0].startswith("event: done\n")


@pytest.mark.asyncio
async def test_slow_subscriber_receives_done_even_when_its_queue_is_full(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    stream = streaming.ActiveStream(conversation_id=store.create_conversation())
    queue = asyncio.Queue(maxsize=1)
    stream.subscribers.append(queue)
    monkeypatch.setattr(streaming, "store", store)
    monkeypatch.setattr(streaming, "_schedule_memory_distillation", lambda *_: None)

    class Backend:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        @asynccontextmanager
        async def stream(self, *_args, **_kwargs):
            async def body():
                yield streaming._format_sse("delta", {"seq": 1, "delta": {"content": "One"}})
                yield streaming._format_sse("done", {"seq": 2, "status": "completed", "messages": [
                    {"role": "assistant", "content": "One"},
                ]})

            yield SimpleNamespace(status_code=200, aiter_text=body)

    monkeypatch.setattr(streaming, "httpx", SimpleNamespace(
        AsyncClient=lambda **_: Backend(), Timeout=lambda *_: None, ConnectError=ConnectionError,
    ))
    await streaming._proxy_backend_stream(stream, {"conversation_id": stream.conversation_id})

    event = queue.get_nowait()
    assert event[0] == "done"
    reply, = event[1]["messages"]
    assert (reply["role"], reply["content"]) == ("assistant", "One")
