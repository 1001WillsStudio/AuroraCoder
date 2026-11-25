import os

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


def test_memory_trace_id_survives_portable_storage_and_index_recovery(tmp_path):
    cid = "memory-maintenance:consolidation:maint-consolidation-012345abcd"
    messages = [{"role": "user", "content": "Retain the maintenance trace"}]
    store = ConversationStore(tmp_path)
    store.create_conversation(conversation_id=cid, conv_type="memory_maintenance")
    store.save_messages(cid, messages)
    assert (tmp_path / "~memory-maintenance~consolidation~maint-consolidation-012345abcd.json").exists()

    (tmp_path / "index.json").write_text("{", encoding="utf-8")
    rebuilt = ConversationStore(tmp_path)
    assert rebuilt.get_conversation(cid)["type"] == "memory_maintenance"
    assert rebuilt.get_messages(cid) == messages


@pytest.mark.skipif(os.name == "nt", reason="Historical Linux filenames used colons")
def test_historical_linux_memory_trace_stays_readable(tmp_path):
    cid = "memory-maintenance:extraction:maint-extraction-012345abcd"
    history = tmp_path / f"{cid}.json"
    history.write_text('[{"role":"user","content":"Historical trace"}]', encoding="utf-8")
    (tmp_path / "index.json").write_text("{", encoding="utf-8")

    rebuilt = ConversationStore(tmp_path)
    assert rebuilt.get_conversation(cid)["type"] == "memory_maintenance"
    assert rebuilt.get_messages(cid)[0]["content"] == "Historical trace"


def test_requests_report_invalid_conversation_id_without_starting_backend(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    monkeypatch.setattr(routes, "store", store)
    client = TestClient(app)
    assert client.post("/api/chat", json={"conversation_id": 170, "message": "Test"}).status_code == 400
    assert client.put("/api/conversations/index", json={"messages": []}).status_code == 400
    assert store.list_conversations() == []
