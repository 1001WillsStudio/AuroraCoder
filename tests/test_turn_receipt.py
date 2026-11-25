from uuid import uuid4

import httpx
import pytest

from gateway import routes
from gateway.api import app
from gateway.conversation_store import ConversationStore


@pytest.mark.asyncio
async def test_reconnect_with_other_turn_receipt_cannot_replay_old_messages(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    cid = store.create_conversation()
    store.save_messages(cid, [{"role": "user", "content": "Previous completed turn"}])
    store.update_status(cid, "completed")
    monkeypatch.setattr(routes, "store", store)
    monkeypatch.setattr(routes, "active_streams", {})
    path = f"/api/conversations/{cid}/stream"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        current = await client.get(path, params={"turn_id": str(uuid4())})
        assert current.status_code == 409
        assert "Previous completed turn" not in current.text
        old_client = await client.get(path)
        assert "Previous completed turn" in old_client.text


@pytest.mark.asyncio
async def test_only_latest_accepted_turn_replays_after_stream_and_store_reload(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    cid = store.create_conversation()
    registry = {}
    monkeypatch.setattr(routes, "store", store)
    monkeypatch.setattr(routes, "active_streams", registry)
    observed = []

    async def backend(stream, body):
        observed.append(body["message"])
        data = {"conversation_id": cid, "status": "completed"}
        stream.finished = True
        stream.latest_event_type = "done"
        stream.latest_event_data = data
        for queue in stream.subscribers:
            queue.put_nowait(("done", data))
        routes.store.update_status(cid, "completed")
        registry.pop(cid)

    monkeypatch.setattr(routes, "_proxy_backend_stream", backend)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        path = f"/api/conversations/{cid}/stream"
        previous, latest = str(uuid4()), str(uuid4())
        for receipt, content in ((previous, "First turn"), (latest, "Second turn")):
            response = await client.post("/api/chat", json={
                "conversation_id": cid, "message": content, "prefer_saved_history": True,
            }, headers={"X-Aurora-Turn-ID": receipt})
            assert response.status_code == 200
        assert observed == ["First turn", "Second turn"]

        monkeypatch.setattr(routes, "store", ConversationStore(tmp_path))
        assert (await client.get(path, params={"turn_id": previous})).status_code == 409
        recovered = await client.get(path, params={"turn_id": latest})
        assert recovered.status_code == 200
        assert recovered.text.count("Second turn") >= 1


@pytest.mark.asyncio
async def test_retry_distinguishes_unaccepted_current_and_superseded_receipts(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    cid = str(uuid4())
    registry = {}
    observed = []
    monkeypatch.setattr(routes, "store", store)
    monkeypatch.setattr(routes, "active_streams", registry)

    async def backend(stream, body):
        observed.append(body["message"])
        data = {"conversation_id": cid, "status": "completed"}
        stream.finished = True
        stream.latest_event_type = "done"
        stream.latest_event_data = data
        for queue in stream.subscribers:
            queue.put_nowait(("done", data))
        routes.store.update_status(cid, "completed")
        registry.pop(cid)

    monkeypatch.setattr(routes, "_proxy_backend_stream", backend)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        async def retry(receipt, text):
            return await client.post("/api/chat", json={
                "conversation_id": cid, "message": text, "prefer_saved_history": True,
                "retry": True, "retry_receipt": True,
            }, headers={"X-Aurora-Turn-ID": receipt})

        first, second = str(uuid4()), str(uuid4())
        assert (await retry(first, "Undelivered first message")).status_code == 200
        assert (await retry(first, "Undelivered first message")).status_code == 200
        assert (await retry(second, "Undelivered follow-up")).status_code == 200
        monkeypatch.setattr(routes, "store", ConversationStore(tmp_path))
        assert (await retry(first, "Undelivered first message")).status_code == 409
        assert observed == ["Undelivered first message", "Undelivered follow-up"]
        routes.store.update_status(cid, "error")
        assert (await retry(second, "Undelivered follow-up")).status_code == 200
        assert observed[-1] is None
        assert [message["content"] for message in routes.store.get_messages(cid)
                if message["role"] == "user"] == observed[:2]
