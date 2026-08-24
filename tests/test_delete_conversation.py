"""Deleting conversations from history.

The product stored every chat forever. DELETE /api/conversations/{id}
existed, but no UI called it, and the store left subagent children behind.
These tests lock cascade delete, the gateway route, and the UI helpers
that decide whether the open chat should close.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gateway.conversation_store import ConversationStore
from gateway.workspace import file_snapshots, snapshot_file

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "frontend" / "src" / "utils" / "conversationDelete.js"
APP = ROOT / "frontend" / "src" / "App.jsx"
HISTORY = ROOT / "frontend" / "src" / "components" / "ConversationHistory.jsx"
API_JS = ROOT / "frontend" / "src" / "services" / "api.js"
MOBILE_APP = ROOT / "mobile" / "js" / "app.js"
MOBILE_API = ROOT / "mobile" / "js" / "api.js"
ROUTES = ROOT / "gateway" / "routes.py"


def _eval_js(expr: str):
    script = (
        "import {\n"
        "  collectDeletionIds,\n"
        "  childCountForDelete,\n"
        "  deletedIdsFromResponse,\n"
        "  openChatWasDeleted,\n"
        "  nextOpenConversationId,\n"
        f"}} from {json.dumps(HELPER.resolve().as_uri())}\n"
        f"const out = {expr}\n"
        "console.log(JSON.stringify(out))\n"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout or "node helper failed")
    return json.loads(proc.stdout)


def _plant(store: ConversationStore, parent_title: str = "fix auth"):
    parent = store.create_conversation()
    store.save_messages(parent, [{"role": "user", "content": parent_title}])
    child = store.create_conversation(parent_id=parent, conv_type="subagent")
    store.save_messages(child, [{"role": "user", "content": "sub task"}])
    store.save_frontend_messages(parent, [{"role": "user", "content": parent_title}])
    store.save_frontend_messages(child, [{"role": "user", "content": "sub task"}])
    return parent, child


@pytest.mark.unit
def test_store_delete_cascades_to_subagent_children(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    parent, child = _plant(store)
    grandchild = store.create_conversation(parent_id=child, conv_type="subagent")
    store.save_messages(grandchild, [{"role": "user", "content": "nested"}])

    deleted = store.delete_conversation(parent)
    assert parent in deleted
    assert child in deleted
    assert grandchild in deleted
    with pytest.raises(KeyError):
        store.get_conversation(parent)
    with pytest.raises(KeyError):
        store.get_conversation(child)
    remaining = {c["id"] for c in store.list_conversations()}
    assert remaining == set()
    assert not store._messages_path(parent).exists()
    assert not store._frontend_messages_path(child).exists()


@pytest.mark.unit
def test_store_delete_child_leaves_parent(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    parent, child = _plant(store)
    deleted = store.delete_conversation(child)
    assert deleted == [child]
    assert store.get_conversation(parent)["title"] == "fix auth"


@pytest.mark.unit
def test_store_delete_missing_raises(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    with pytest.raises(KeyError):
        store.delete_conversation("missing-id")


@pytest.mark.unit
def test_late_frontend_persist_does_not_resurrect(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    parent, _child = _plant(store)
    store.delete_conversation(parent)
    store.save_frontend_messages(parent, [{"role": "user", "content": "zombie"}])
    assert parent not in {c["id"] for c in store.list_conversations()}
    assert not store._frontend_messages_path(parent).exists()


@pytest.mark.unit
def test_unlink_drops_files_written_after_delete_won_the_race(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    parent, _child = _plant(store)
    store.delete_conversation(parent)
    path = store._messages_path(parent)
    path.write_text("[]", encoding="utf-8")
    store._unlink_if_missing_from_index(parent)
    assert not path.exists()
    leftover = store.create_conversation()
    store._unlink_if_missing_from_index(leftover)
    assert leftover in {c["id"] for c in store.list_conversations()}


@pytest.mark.unit
def test_delete_route_cancels_subtree_and_clears_snapshots(tmp_path, monkeypatch):
    isolated = ConversationStore(tmp_path / "conversations")
    parent, child = _plant(isolated)
    snapshot_file(parent, "src/main.py", "print(1)\n")
    cancelled: list[str] = []

    async def fake_cancel(cid: str):
        cancelled.append(cid)

    monkeypatch.setattr("gateway.routes.store", isolated)
    monkeypatch.setattr("gateway.routes._cancel_active_stream", fake_cancel)

    from fastapi.testclient import TestClient
    from gateway.api import app

    client = TestClient(app)
    try:
        missing = client.delete("/api/conversations/not-a-real-id")
        assert missing.status_code == 404

        response = client.delete(f"/api/conversations/{parent}")
        assert response.status_code == 200
        body = response.json()
        assert body["deleted"] == parent
        assert set(body["deleted_ids"]) == {parent, child}
        assert set(cancelled) == {parent, child}
        assert parent not in file_snapshots
        with pytest.raises(KeyError):
            isolated.get_conversation(parent)
    finally:
        file_snapshots.pop(parent, None)
        file_snapshots.pop(child, None)


@pytest.mark.unit
def test_collect_deletion_ids_includes_nested_children():
    convs = [
        {"id": "p", "parent_id": None},
        {"id": "c1", "parent_id": "p"},
        {"id": "c2", "parent_id": "p"},
        {"id": "g", "parent_id": "c1"},
        {"id": "other", "parent_id": None},
    ]
    ids = _eval_js(f"collectDeletionIds({json.dumps(convs)}, 'p')")
    assert ids[0] == "p"
    assert set(ids) == {"p", "c1", "c2", "g"}
    assert _eval_js(f"childCountForDelete({json.dumps(convs)}, 'p')") == 3
    assert _eval_js("collectDeletionIds([], 'stale')") == ["stale"]


@pytest.mark.unit
def test_next_open_conversation_after_delete():
    assert _eval_js("nextOpenConversationId('open', null, ['other'])") == "open"
    assert _eval_js("nextOpenConversationId('child', 'parent', ['child'])") == "parent"
    assert _eval_js("nextOpenConversationId('parent', null, ['parent', 'child'])") is None
    assert _eval_js("nextOpenConversationId('child', 'parent', ['parent', 'child'])") is None
    assert _eval_js("openChatWasDeleted('child', ['parent', 'child'])") is True
    assert _eval_js("openChatWasDeleted('open', ['other'])") is False


@pytest.mark.unit
def test_deleted_ids_from_response_shapes():
    assert _eval_js(
        "deletedIdsFromResponse({deleted: 'a', deleted_ids: ['a', 'b']}, 'z')"
    ) == ["a", "b"]
    assert _eval_js("deletedIdsFromResponse({deleted: 'a'}, 'z')") == ["a"]
    assert _eval_js("deletedIdsFromResponse({}, 'z')") == ["z"]


@pytest.mark.unit
def test_ui_wires_delete_through_history_and_api():
    app = APP.read_text(encoding="utf-8")
    history = HISTORY.read_text(encoding="utf-8")
    api = API_JS.read_text(encoding="utf-8")
    routes = ROUTES.read_text(encoding="utf-8")
    mobile_app = MOBILE_APP.read_text(encoding="utf-8")
    mobile_api = MOBILE_API.read_text(encoding="utf-8")

    assert "data-testid=\"conversation-delete\"" in history
    assert "data-testid=\"conversation-delete-confirm\"" in history
    assert "childCountForDelete" in history
    assert "method: 'DELETE'" in api
    assert "export async function deleteConversation" in api
    assert "handleDeleteConversation" in app
    assert "nextOpenConversationId" in app
    assert "onDeleteConversation={handleDeleteConversation}" in app
    assert "store.ids_in_subtree" in routes
    assert "store.delete_conversation" in routes
    assert "API.deleteConversation" in mobile_app
    assert "conversation-item-delete" in mobile_app
    assert "method: 'DELETE'" in mobile_api
    translations = (ROOT / "frontend" / "src" / "i18n" / "translations.js").read_text(
        encoding="utf-8"
    )
    for key in (
        "'history.delete'",
        "'history.deleteConfirm'",
        "'history.deleteConfirmWithChildren'",
        "'history.deleteCancel'",
    ):
        assert translations.count(key) == 2, f"{key} must exist in en and zh"
