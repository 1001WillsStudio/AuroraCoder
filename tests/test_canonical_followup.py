import asyncio

import pytest

from gateway import routes
from gateway.conversation_store import ConversationStore


@pytest.mark.asyncio
@pytest.mark.parametrize(("saved", "prefer_saved_history"), [(True, True), (False, True), (True, False)])
async def test_saved_history_beats_a_stale_desktop_snapshot_but_preserves_forks(
    tmp_path, monkeypatch, saved, prefer_saved_history,
):
    store = ConversationStore(tmp_path)
    cid = "desktop-history"
    snapshot = [{"role": "user", "content": "First accepted message"}]
    newer = {"role": "user", "content": "An instruction accepted from the other tab"}
    if saved:
        store.create_conversation(conversation_id=cid)
        store.save_messages(cid, [*snapshot, newer])
    observed = []

    class Request:
        async def json(self):
            return {
                "conversation_id": cid, "messages": snapshot,
                "message": "A follow-up from this tab", "prefer_saved_history": prefer_saved_history,
            }

    async def start_backend(_, body):
        observed.append(body)

    monkeypatch.setattr(routes, "store", store)
    monkeypatch.setattr(routes, "active_streams", {})
    monkeypatch.setattr(routes, "_proxy_backend_stream", start_backend)
    response = await routes.proxy_chat(Request())
    await asyncio.sleep(0)
    assert response.status_code == 200
    expected = [message["content"] for message in snapshot]
    if saved and prefer_saved_history:
        expected.append(newer["content"])
    assert [message["content"] for message in observed[0]["messages"]] == expected
    assert "prefer_saved_history" not in observed[0]
    assert [message["content"] for message in store.get_messages(cid)] == [
        *expected, "A follow-up from this tab",
    ]
