import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from gateway import routes, streaming
from gateway.conversation_store import ConversationStore


@pytest.mark.asyncio
async def test_concurrent_sends_wait_to_replace_the_same_stream(tmp_path, monkeypatch):
    history = ConversationStore(tmp_path)
    cid = history.create_conversation()
    active = {}
    entered = []
    first_entered, release_first = asyncio.Event(), asyncio.Event()

    async def finish_previous(conversation_id):
        entered.append(conversation_id)
        if len(entered) == 1:
            first_entered.set()
            await release_first.wait()

    async def backend(*_):
        pass

    class Request:
        async def json(self):
            return {"conversation_id": cid, "message": "New turn", "max_iterations": 2,
                    "messages": [{"role": "user", "content": "Prior turn"}]}

    monkeypatch.setattr(routes, "store", history)
    monkeypatch.setattr(routes, "active_streams", active)
    monkeypatch.setattr(routes, "_get_workspace", lambda: tmp_path)
    monkeypatch.setattr(routes, "_cancel_active_stream", finish_previous)
    monkeypatch.setattr(routes, "_proxy_backend_stream", backend)

    first = asyncio.create_task(routes.proxy_chat(Request()))
    await asyncio.wait_for(first_entered.wait(), 2)
    second = asyncio.create_task(routes.proxy_chat(Request()))
    try:
        await asyncio.sleep(0)
        assert entered == [cid]
    finally:
        release_first.set()
        await asyncio.wait_for(asyncio.gather(first, second), 2)
    assert entered == [cid, cid]


@pytest.mark.asyncio
async def test_superseded_proxy_cannot_replace_newer_history_during_cleanup(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    cid = store.create_conversation()
    old = streaming.ActiveStream(conversation_id=cid)
    new = streaming.ActiveStream(conversation_id=cid)
    active = {cid: old}
    previous = [{"role": "user", "content": "Previous"}]
    current = [{"role": "user", "content": "Current"}]
    monkeypatch.setattr(streaming, "store", store)
    monkeypatch.setattr(streaming, "active_streams", active)
    monkeypatch.setattr(streaming, "_schedule_memory_distillation", lambda *_: None)

    class Backend:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        @asynccontextmanager
        async def stream(self, *_args, **_kwargs):
            async def body():
                yield streaming._format_sse("messages", {
                    "status": "running", "messages": previous, "raw_messages": previous,
                })
                old.cancel_event.set()
                active[cid] = new
                store.save_messages(cid, current)
                store.save_frontend_messages(cid, current)
                store.update_status(cid, "running")

            yield SimpleNamespace(status_code=200, aiter_text=body)

    monkeypatch.setattr(streaming, "httpx", SimpleNamespace(
        AsyncClient=lambda **_: Backend(), Timeout=lambda *_: None, ConnectError=ConnectionError,
    ))

    await streaming._proxy_backend_stream(old, {"conversation_id": cid})

    assert active[cid] is new
    assert store.get_conversation(cid)["status"] == "running"
    assert store.get_messages(cid)[-1]["content"] == "Current"
    assert store.get_frontend_messages(cid)[-1]["content"] == "Current"


@pytest.mark.asyncio
async def test_cancel_releases_conversation_when_backend_delays_cancellation(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    cid = store.create_conversation()
    stream = streaming.ActiveStream(conversation_id=cid)
    active = {cid: stream}
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed_backend():
        try:
            started.set()
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()

    monkeypatch.setattr(streaming, "_CANCEL_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(streaming, "active_streams", active)
    monkeypatch.setattr(streaming, "store", store)
    stream.task = asyncio.create_task(delayed_backend())
    await asyncio.wait_for(started.wait(), 1)
    try:
        await asyncio.wait_for(streaming._cancel_active_stream(cid), 0.5)
        assert not stream.task.done()
        assert cid not in active
        assert store.get_conversation(cid)["status"] == "interrupted"
    finally:
        release.set()
        await asyncio.wait_for(stream.task, 1)
