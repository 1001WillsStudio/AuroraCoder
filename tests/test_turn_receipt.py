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


@pytest.mark.asyncio
async def test_restart_between_raw_intake_and_display_write_recovers_same_turn(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    cid = store.create_conversation()
    earlier_id, current_id = str(uuid4()), str(uuid4())
    store.save_messages(cid, [{"role": "user", "content": "First accepted turn"}])
    store.seed_frontend_user_message(cid, "First accepted turn")
    store.update_status(cid, "completed", accepted_turn_id=earlier_id)
    monkeypatch.setattr(routes, "store", store)
    monkeypatch.setattr(routes, "active_streams", {})

    def fail_display_write(*_args, **_kwargs):
        raise OSError("synthetic process exit after raw transcript write")

    monkeypatch.setattr(store, "seed_frontend_user_message", fail_display_write)
    model_requests = []

    async def backend(stream, body):
        model_requests.append(body)
        data = {"conversation_id": cid, "status": "completed"}
        stream.finished = True
        stream.latest_event_type = "done"
        stream.latest_event_data = data
        for queue in stream.subscribers:
            queue.put_nowait(("done", data))
        routes.store.update_status(cid, "completed")
        routes.active_streams.pop(cid)

    monkeypatch.setattr(routes, "_proxy_backend_stream", backend)
    payload = {"conversation_id": cid, "message": "Accepted interruption", "prefer_saved_history": True}
    headers = {"X-Aurora-Turn-ID": current_id}
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
        lost = await client.post("/api/chat", json=payload, headers=headers)
        assert lost.status_code == 500 and not model_requests

        reopened = ConversationStore(tmp_path)
        monkeypatch.setattr(routes, "store", reopened)
        history = (await client.get(f"/api/conversations/{cid}")).json()
        assert history["status"] == "error"
        assert history["accepted_turn_id"] == current_id
        assert any(item.get("isError") for item in history["frontend_messages"])
        assert [item["content"] for item in history["frontend_messages"] if item["role"] == "user"] == [
            "First accepted turn", "Accepted interruption",
        ]
        assert all("pending_turn_id" not in item for item in history["messages"])
        retried = await client.post("/api/chat", json={**payload, "retry": True, "retry_receipt": True}, headers=headers)
        assert retried.status_code == 200
        assert len(model_requests) == 1 and model_requests[0]["message"] is None
        assert [msg["content"] for msg in reopened.get_messages(cid) if msg["role"] == "user"] == [
            "First accepted turn", "Accepted interruption",
        ]
