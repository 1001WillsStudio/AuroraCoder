"""Regression: continue a chat from the gateway, never from a client slice.

Desktop "fork from here" used to slice ``rawMessages`` in the browser, mint a
new id client-side, and only persist anything once the user sent the next
message.  A refresh before that first send lost the fork point (and the URL),
and any other client had to copy raw history by hand to continue a chat in a
new session.

The gateway now owns the copy: ``POST /api/conversations/{id}/fork`` takes an
optional zero-based *stored* user-message ordinal, copies everything before
that user message, and persists the new conversation immediately.  Provenance
lives in ``forked_from_id`` — deliberately NOT ``parent_id``, which is the
subagent link that cascades on delete and is filtered out of the user's list.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gateway import routes
from gateway.api import app
from gateway.conversation_store import (
    ConversationStore,
    InvalidForkPoint,
    slice_history_for_fork,
)
from gateway.streaming import ActiveStream

SOURCE = "11111111-2222-4333-8444-555555555555"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _raw_transcript():
    """Two user turns; the first one has a complete tool round."""
    return [
        {"role": "user", "content": "First question"},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "c1", "function": {"name": "read_file", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "file body"},
        {"role": "assistant", "content": "First answer"},
        {"role": "user", "content": "Second question"},
        {"role": "assistant", "content": "Second answer"},
    ]


def _frontend_transcript():
    return [
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "First answer"},
        {"role": "user", "content": "Second question"},
        {"role": "assistant", "content": "Second answer"},
    ]


def _seed(store, raw=None, frontend=None, cid=SOURCE, status="completed"):
    store.create_conversation(conversation_id=cid, provider_id="openai")
    store.save_messages(cid, raw if raw is not None else _raw_transcript())
    store.save_frontend_messages(cid, frontend if frontend is not None else _frontend_transcript())
    store.update_status(cid, status)
    return cid


def _bubbles(messages):
    """Frontend bubbles without the storage timestamps."""
    return [{"role": m.get("role"), "content": m.get("content")} for m in messages]


def _assert_valid_tool_rounds(messages):
    """Every assistant tool_call must be followed by exactly its results."""
    i = 0
    while i < len(messages):
        message = messages[i]
        assert message.get("role") != "tool", f"orphan tool result at {i}"
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        if not calls:
            i += 1
            continue
        expected = [call["id"] for call in calls]
        results = []
        j = i + 1
        while j < len(messages) and messages[j].get("role") == "tool":
            results.append(messages[j]["tool_call_id"])
            j += 1
        assert results == expected
        i = j


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Route-level store isolated from the real data dir."""
    isolated = ConversationStore(tmp_path / "conversations")
    monkeypatch.setattr(routes, "store", isolated)
    monkeypatch.setattr("gateway.streaming.store", isolated)
    monkeypatch.setattr(routes, "active_streams", {})
    return isolated


@pytest.fixture
def client(isolated_store):
    return TestClient(app)


# ---------------------------------------------------------------------------
# Store: fork semantics
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_fork_all_history_persists_immediately(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    _seed(store)
    source_before = store.get_messages(SOURCE)
    source_frontend_before = store.get_frontend_messages(SOURCE)

    fork = store.fork_conversation(SOURCE)

    assert fork["forked_from_id"] == SOURCE
    assert fork["id"] != SOURCE
    assert [m.get("content") for m in fork["messages"]] == [
        "First question", None, "file body", "First answer",
        "Second question", "Second answer",
    ]
    _assert_valid_tool_rounds(fork["messages"])

    # Persisted before the response is returned — no client send required.
    stored = store.get_conversation(fork["id"])
    assert [m["role"] for m in stored["messages"]] == [
        "user", "assistant", "tool", "assistant", "user", "assistant",
    ]
    assert _bubbles(store.get_frontend_messages(fork["id"])) == _bubbles(_frontend_transcript())
    assert stored["forked_from_id"] == SOURCE
    assert stored["status"] == "completed"
    assert stored["provider_id"] == "openai"

    # Source history stays byte-for-byte what it was.
    assert store.get_messages(SOURCE) == source_before
    assert store.get_frontend_messages(SOURCE) == source_frontend_before


@pytest.mark.unit
def test_fork_before_user_message_zero_is_an_empty_chat(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    _seed(store)

    fork = store.fork_conversation(SOURCE, before_user_message=0)

    assert fork["messages"] == []
    assert fork["frontend_messages"] == []
    stored = store.get_conversation(fork["id"])
    assert stored["messages"] == []
    assert stored["title"] == "Untitled"
    assert stored["status"] == "completed"
    # Still a real, listable conversation that can be continued.
    assert fork["id"] in [c["id"] for c in store.list_conversations(conv_type="user_chat")]


@pytest.mark.unit
def test_fork_ordinal_keeps_earlier_turns_only(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    _seed(store)

    fork = store.fork_conversation(SOURCE, before_user_message=1)

    assert [m.get("content") for m in fork["messages"]] == [
        "First question", None, "file body", "First answer",
    ]
    _assert_valid_tool_rounds(fork["messages"])
    assert _bubbles(store.get_frontend_messages(fork["id"])) == [
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "First answer"},
    ]
    # Ordinal == number of stored user messages means "all history".
    whole = store.fork_conversation(SOURCE, before_user_message=2)
    assert len(whole["messages"]) == len(_raw_transcript())


@pytest.mark.unit
def test_fork_drops_incomplete_tool_rounds(tmp_path):
    """A Stop mid-round leaves an assistant tool_call with no results."""
    store = ConversationStore(tmp_path / "conversations")
    dangling = {"id": "c2", "function": {"name": "run_terminal_command", "arguments": "{}"}}
    _seed(store, raw=[*_raw_transcript(), {"role": "assistant", "tool_calls": [dangling]}])

    fork = store.fork_conversation(SOURCE)

    assert all(
        call["id"] != "c2"
        for message in fork["messages"]
        for call in (message.get("tool_calls") or [])
    )
    _assert_valid_tool_rounds(fork["messages"])
    assert [m["role"] for m in store.get_conversation(fork["id"])["messages"]][-1] == "assistant"


@pytest.mark.unit
def test_fork_rejects_out_of_range_ordinals(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    _seed(store)

    for ordinal in (-1, 3):
        with pytest.raises(InvalidForkPoint):
            store.fork_conversation(SOURCE, before_user_message=ordinal)

    # Nothing half-created by the refused requests.
    assert len([c for c in store.list_conversations() if c["id"] != SOURCE]) == 0


@pytest.mark.unit
def test_slice_history_for_fork_does_not_touch_inputs():
    raw = _raw_transcript()
    frontend = _frontend_transcript()
    raw_snapshot, frontend_snapshot = list(raw), list(frontend)

    raw_slice, frontend_slice = slice_history_for_fork(raw, frontend, 1)
    raw_slice.append({"role": "user", "content": "mutation"})

    assert raw == raw_snapshot
    assert frontend == frontend_snapshot
    assert len(frontend_slice) == 2


@pytest.mark.unit
def test_fork_provenance_is_not_subagent_lineage(tmp_path):
    """``parent_id`` is the cascading subagent link — forks must not use it."""
    store = ConversationStore(tmp_path / "conversations")
    _seed(store)
    child = store.create_conversation(
        conversation_id="child-1", parent_id=SOURCE, conv_type="subagent",
    )
    store.save_messages(child, [{"role": "user", "content": "subagent work"}])

    fork = store.fork_conversation(SOURCE, before_user_message=1)

    assert store.get_conversation(fork["id"])["parent_id"] is None
    assert fork["id"] not in store.ids_in_subtree(SOURCE)
    assert [c["id"] for c in store.get_children(SOURCE)] == ["child-1"]

    deleted = store.delete_conversation(SOURCE)
    assert set(deleted) == {SOURCE, "child-1"}
    survivor = store.get_conversation(fork["id"])
    assert survivor["forked_from_id"] == SOURCE
    assert survivor["messages"]


@pytest.mark.unit
def test_fork_survives_gateway_restart_and_can_continue(tmp_path):
    """A forked chat is on disk before the response, so a restart can resume it."""
    store = ConversationStore(tmp_path / "conversations")
    _seed(store)
    fork = store.fork_conversation(SOURCE, before_user_message=1)

    restarted = ConversationStore(tmp_path / "conversations")
    restored = restarted.get_conversation(fork["id"])
    assert restored["forked_from_id"] == SOURCE
    assert [m.get("content") for m in restored["messages"]] == [
        "First question", None, "file body", "First answer",
    ]
    assert restarted.get_frontend_messages(fork["id"])[0]["content"] == "First question"

    # POST /api/chat continues a chat WITHOUT a transcript by resuming the
    # stored one: ``prior = body.get("messages") or store.get_messages(cid)``.
    body = {"conversation_id": fork["id"], "message": "Continued here"}
    prior = body.get("messages") or restarted.get_messages(body["conversation_id"])
    assert [m.get("content") for m in prior] == [
        "First question", None, "file body", "First answer",
    ]
    accepted = [*prior, {"role": "user", "content": body["message"]}]
    restarted.save_messages(fork["id"], accepted)
    restarted.update_status(fork["id"], "completed")

    assert [m.get("content") for m in restarted.get_messages(fork["id"])][-1] == "Continued here"
    # The source keeps its own history; only the copy grew.
    assert [m.get("content") for m in restarted.get_messages(SOURCE)][-1] == "Second answer"
    assert len(restarted.get_messages(SOURCE)) == len(_raw_transcript())


# ---------------------------------------------------------------------------
# Route: POST /api/conversations/{id}/fork
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_fork_route_returns_persisted_copy(client, isolated_store):
    _seed(isolated_store)

    response = client.post(f"/api/conversations/{SOURCE}/fork", json={"before_user_message": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["forked_from_id"] == SOURCE
    assert body["id"] and body["id"] != SOURCE
    assert [m.get("content") for m in body["messages"]] == [
        "First question", None, "file body", "First answer",
    ]
    assert [m.get("content") for m in body["frontend_messages"]] == [
        "First question", "First answer",
    ]

    # A reload (GET) finds the same copy under the new id.
    reloaded = client.get(f"/api/conversations/{body['id']}")
    assert reloaded.status_code == 200
    assert reloaded.json()["forked_from_id"] == SOURCE
    assert len(reloaded.json()["messages"]) == 4
    assert len(reloaded.json()["frontend_messages"]) == 2


@pytest.mark.unit
def test_fork_route_accepts_blank_body_and_blank_ordinal(client, isolated_store):
    _seed(isolated_store)

    no_body = client.post(f"/api/conversations/{SOURCE}/fork")
    assert no_body.status_code == 200
    assert len(no_body.json()["messages"]) == len(_raw_transcript())

    empty_body = client.post(f"/api/conversations/{SOURCE}/fork", json={})
    assert empty_body.status_code == 200
    assert len(empty_body.json()["messages"]) == len(_raw_transcript())

    null_ordinal = client.post(
        f"/api/conversations/{SOURCE}/fork", json={"before_user_message": None},
    )
    assert null_ordinal.status_code == 200
    assert len(null_ordinal.json()["messages"]) == len(_raw_transcript())

    zero = client.post(f"/api/conversations/{SOURCE}/fork", json={"before_user_message": 0})
    assert zero.status_code == 200
    assert zero.json()["messages"] == []
    assert zero.json()["frontend_messages"] == []

    blank_id = client.post("/api/conversations/%20/fork", json={})
    assert blank_id.status_code == 400


@pytest.mark.unit
def test_fork_route_reports_deleted_conversation(client, isolated_store):
    """A client that forked after the source was deleted gets 404, not a copy."""
    _seed(isolated_store)
    earlier = client.post(
        f"/api/conversations/{SOURCE}/fork", json={"before_user_message": 1},
    ).json()

    assert client.delete(f"/api/conversations/{SOURCE}").status_code == 200

    response = client.post(f"/api/conversations/{SOURCE}/fork", json={})
    assert response.status_code == 404
    # The earlier fork is a separate chat: it outlived its source.
    survivor = client.get(f"/api/conversations/{earlier['id']}")
    assert survivor.status_code == 200
    assert survivor.json()["forked_from_id"] == SOURCE
    assert len(survivor.json()["messages"]) == 4


@pytest.mark.unit
def test_fork_route_copies_only_committed_history_while_active(
    client, isolated_store, monkeypatch,
):
    """Forking mid-run must not copy the in-memory preview or look "running"."""
    _seed(isolated_store)
    stream = ActiveStream(SOURCE)
    stream.latest_frontend_messages = [
        *_frontend_transcript(),
        {"role": "assistant", "content": "half-written preview"},
    ]
    monkeypatch.setattr(routes, "active_streams", {SOURCE: stream})
    isolated_store.update_status(SOURCE, "running")

    fork = client.post(f"/api/conversations/{SOURCE}/fork", json={"before_user_message": 2})

    assert fork.status_code == 200
    body = fork.json()
    assert body["status"] == "completed"
    assert "half-written preview" not in [m.get("content") for m in body["frontend_messages"]]
    assert len(body["messages"]) == len(_raw_transcript())
    # The fork is independent: it is not the live conversation's id.
    assert body["id"] != SOURCE


@pytest.mark.unit
def test_fork_route_recovers_a_stale_running_conversation(client, isolated_store):
    """A gateway killed mid-turn leaves status="running" with no live stream."""
    _seed(isolated_store, status="running")

    stale = client.post(f"/api/conversations/{SOURCE}/fork", json={})

    assert stale.status_code == 200
    assert stale.json()["status"] == "completed"
    assert len(stale.json()["messages"]) == len(_raw_transcript())


@pytest.mark.unit
def test_fork_route_rejects_stale_ordinals(client, isolated_store):
    _seed(isolated_store)

    too_far = client.post(
        f"/api/conversations/{SOURCE}/fork", json={"before_user_message": 9},
    )
    assert too_far.status_code == 400
    assert "out of range" in too_far.json()["detail"]

    negative = client.post(
        f"/api/conversations/{SOURCE}/fork", json={"before_user_message": -1},
    )
    assert negative.status_code == 400

    garbage = client.post(
        f"/api/conversations/{SOURCE}/fork", json={"before_user_message": "third"},
    )
    assert garbage.status_code == 422


@pytest.mark.unit
def test_fork_route_requires_authentication(client, isolated_store, monkeypatch):
    import gateway.api as api_module

    _seed(isolated_store)
    monkeypatch.setattr(api_module, "AUTH_PASSWORD", "hunter2")

    unauthorized = client.post(f"/api/conversations/{SOURCE}/fork", json={})
    assert unauthorized.status_code == 401

    login = client.post("/api/auth/login", json={"password": "hunter2"})
    assert login.status_code == 200
    token = login.json()["token"]

    authorized = client.post(
        f"/api/conversations/{SOURCE}/fork",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert authorized.status_code == 200
    assert authorized.json()["forked_from_id"] == SOURCE


@pytest.mark.unit
async def test_fork_then_continue_streams_the_copied_history(
    client, isolated_store, monkeypatch,
):
    """The normal /api/chat entry point resumes the fork with NO client transcript."""
    import asyncio
    import json
    from types import SimpleNamespace

    import httpx

    from gateway import streaming

    _seed(isolated_store)
    fork = client.post(
        f"/api/conversations/{SOURCE}/fork", json={"before_user_message": 1},
    ).json()

    seen = {}

    class BackendStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield streaming._format_sse("done", {
                "status": "completed",
                "messages": [{"role": "assistant", "content": "done"}],
                "raw_messages": [],
            }).encode()

    def handler(request):
        body = json.loads(request.content)
        seen["messages"] = body["messages"]
        seen["message"] = body["message"]
        return httpx.Response(200, stream=BackendStream())

    def AsyncClient(**kwargs):
        kwargs.pop("timeout", None)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(streaming, "httpx", SimpleNamespace(
        AsyncClient=AsyncClient,
        Timeout=httpx.Timeout,
        ConnectError=httpx.ConnectError,
        HTTPError=httpx.HTTPError,
    ))
    monkeypatch.setattr(streaming, "_schedule_memory_distillation", lambda *args: None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway",
    ) as api:
        response = await asyncio.wait_for(
            api.post(
                "/api/chat",
                json={"conversation_id": fork["id"], "message": "Continue here"},
            ),
            5,
        )

    assert response.status_code == 200
    assert response.headers["X-Conversation-ID"] == fork["id"]
    assert seen["message"] == "Continue here"
    # The gateway resumed the COPIED history — the client sent no transcript,
    # and the source's later turn ("Second question") is not part of it.
    assert [m.get("content") for m in seen["messages"]] == [
        "First question", None, "file body", "First answer",
    ]
    # Only the fork grew; the source keeps its own tail.
    assert [m.get("content") for m in isolated_store.get_messages(fork["id"])][-1] == "Continue here"
    assert [m.get("content") for m in isolated_store.get_messages(SOURCE)][-1] == "Second answer"
