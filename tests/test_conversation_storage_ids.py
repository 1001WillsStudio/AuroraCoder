import pytest
from fastapi.testclient import TestClient

from gateway import routes
from gateway.api import app
from gateway.conversation_store import ConversationStore, InvalidConversationId


@pytest.mark.parametrize("value", ["index", "INDEX", "notes.frontend", "a/b", "COM1", 123])
def test_storage_ids_must_be_distinct_safe_filenames(tmp_path, value):
    store = ConversationStore(tmp_path)
    with pytest.raises(InvalidConversationId):
        store.create_conversation(conversation_id=value)
    assert store.list_conversations() == []


def test_legacy_safe_id_round_trips_and_case_alias_is_rejected(tmp_path):
    store = ConversationStore(tmp_path)
    cid = store.create_conversation(conversation_id="Desk-Chat_1.2")
    store.save_messages(cid, [{"role": "user", "content": "Retain this turn"}])
    with pytest.raises(InvalidConversationId):
        store.create_conversation(conversation_id="desk-chat_1.2")

    reloaded = ConversationStore(tmp_path)
    assert reloaded.get_messages(cid)[0]["content"] == "Retain this turn"


def test_requests_report_invalid_conversation_id_without_starting_backend(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    monkeypatch.setattr(routes, "store", store)
    client = TestClient(app)
    assert client.post("/api/chat", json={"conversation_id": 170, "message": "Test"}).status_code == 400
    assert client.put("/api/conversations/index", json={"messages": []}).status_code == 400
    assert store.list_conversations() == []
