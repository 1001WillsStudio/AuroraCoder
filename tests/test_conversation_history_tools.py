"""Cross-conversation history tools: scope, pagination, search, and wiring.

Covers the first user-facing cross-conversation workflow:

  * the shared search helper finds Chinese + English terms deep in old chats
    and returns snippets near the match;
  * the gateway read API is metadata/transcript paginated, excludes the
    current chat and private agent transcripts, and never leaks tool output,
    system prompts or reasoning;
  * the per-turn credential — not the user's login token — authenticates the
    three internal routes, and dies with the turn;
  * the real backend agent loop reaches the real gateway routes end to end.
"""

import json
import os
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway import conversation_history as history
from gateway.conversation_store import ConversationStore
from memory.ops import conversation_search
from src import main_flow
from src.core_tools import conversation_history as tools_client


def _seed(store, cid, messages, conv_type="user_chat", parent_id=None):
    store.create_conversation(
        conversation_id=cid, conv_type=conv_type, parent_id=parent_id,
    )
    store.save_messages(cid, messages)


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Isolated conversation store shared by the gateway module and search."""
    store = ConversationStore(tmp_path / "history")
    monkeypatch.setattr(history, "store", store)
    monkeypatch.setattr(conversation_search, "store", store)
    history.clear_turn_credentials()
    conversation_search.clear_visible_transcript_cache()
    yield store
    conversation_search.clear_visible_transcript_cache()
    history.clear_turn_credentials()


# ---------------------------------------------------------------------------
# Projection + transcript pagination
# ---------------------------------------------------------------------------

def _rich_transcript():
    return [
        {"role": "system", "content": "SYSTEM SECRET"},
        {
            "role": "user",
            "content": "[TASK INSTRUCTION]\ninternal context\n[/TASK INSTRUCTION]\n"
                       "Where is the deployment runbook?",
        },
        {
            "role": "assistant", "content": "", "reasoning_content": "THINKING SECRET",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "run_terminal_command", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "TOOL SECRET output"},
        {"role": "assistant", "content": "Draft answer that was retracted"},
        {"role": "assistant", "content": "The runbook lives in docs/deploy.md."},
        {
            "role": "user",
            "content": "[ATTACHED FILES]\n- src/app.py\n[/ATTACHED FILES]\nThanks",
        },
        {"role": "assistant", "content": "You're welcome."},
    ]


def test_read_returns_only_user_and_final_assistant_text(isolated_store):
    store = isolated_store
    _seed(store, "chat-read", _rich_transcript())

    page = history.read_conversation_transcript(
        "chat-read", exclude_conversation_id="chat-current", limit=10,
    )

    assert page is not None
    assert [(m["role"], m["text"]) for m in page["messages"]] == [
        ("user", "Where is the deployment runbook?"),
        ("assistant", "The runbook lives in docs/deploy.md."),
        ("user", "Thanks"),
        ("assistant", "You're welcome."),
    ]
    assert [m["position"] for m in page["messages"]] == [1, 5, 6, 7]
    assert page["total_messages"] == 4
    assert "SECRET" not in json.dumps(page, ensure_ascii=False)
    assert "TASK INSTRUCTION" not in json.dumps(page, ensure_ascii=False)


def _tool_round(call_id, content=""):
    """Assistant message that requests a tool, committed *before* the tool runs.

    The live loop persists this message as soon as the model asks for a tool,
    so it can carry narration in ``content`` alongside its ``tool_calls``.
    """
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {"id": call_id, "type": "function",
             "function": {"name": "grep_search", "arguments": "{}"}},
        ],
    }


def test_interrupted_tool_round_progress_text_never_becomes_final_answer(isolated_store):
    """A tool round with no answer after it must not replay its narration.

    Interrupted and errored turns leave the committed tool-call message as the
    transcript's last assistant entry.  Its ``content`` is progress prose, not
    the turn's decision, so history reads/searches must not present it as one.
    """
    store = isolated_store
    marker = "PROGRESS MARKER 7c41 checking the runbook index"
    follow_up = "PROGRESS MARKER 7c41 checking the rollback drill"

    # Shape 1: the interrupted round is the transcript's final message.
    _seed(store, "chat-interrupted-last", [
        {"role": "user", "content": "Which runbook covers deploys?"},
        _tool_round("c1", content=marker),
        {"role": "tool", "tool_call_id": "c1", "content": "TOOL output"},
    ])
    # Shape 2: the user tries again, so the next turn starts over the round.
    _seed(store, "chat-interrupted-then-next", [
        {"role": "user", "content": "Which runbook covers rollbacks?"},
        _tool_round("c1", content=follow_up),
        {"role": "tool", "tool_call_id": "c1", "content": "TOOL output"},
        {"role": "user", "content": "Still there?"},
        {"role": "assistant", "content": "Yes — checking now."},
    ])

    last = history.read_conversation_transcript("chat-interrupted-last", limit=10)
    assert [(m["role"], m["text"]) for m in last["messages"]] == [
        ("user", "Which runbook covers deploys?"),
    ]
    assert marker not in json.dumps(last, ensure_ascii=False)

    next_turn = history.read_conversation_transcript(
        "chat-interrupted-then-next", limit=10,
    )
    assert [(m["role"], m["text"], m["position"]) for m in next_turn["messages"]] == [
        ("user", "Which runbook covers rollbacks?", 0),
        ("user", "Still there?", 3),
        ("assistant", "Yes — checking now.", 4),
    ]

    # Search must not surface the progress text as a prior decision either.
    assert conversation_search.search_conversation_transcripts(
        "PROGRESS MARKER 7c41",
    ) == []


def test_transcript_pagination_positions_stay_stable_as_chat_grows(isolated_store):
    store = isolated_store
    messages = _rich_transcript()
    _seed(store, "chat-grow", messages)

    first = history.read_conversation_transcript("chat-grow", limit=2)
    assert [m["position"] for m in first["messages"]] == [1, 5]
    assert first["next_cursor"] == 5
    second = history.read_conversation_transcript("chat-grow", after=5, limit=2)
    assert [m["position"] for m in second["messages"]] == [6, 7]
    assert second["next_cursor"] is None

    # A later turn appends messages; already-read positions must not shift.
    messages.extend([
        {"role": "user", "content": "And staging?"},
        {"role": "assistant", "content": "Staging uses the same script."},
    ])
    store.save_messages("chat-grow", messages)

    again = history.read_conversation_transcript("chat-grow", limit=2)
    assert [m["position"] for m in again["messages"]] == [1, 5]
    tail = history.read_conversation_transcript("chat-grow", after=again["next_cursor"], limit=2)
    assert [m["position"] for m in tail["messages"]] == [6, 7]
    assert tail["next_cursor"] == 7
    final = history.read_conversation_transcript("chat-grow", after=7, limit=2)
    assert [m["text"] for m in final["messages"]] == [
        "And staging?", "Staging uses the same script.",
    ]


def test_read_scope_excludes_current_chat_children_and_unknown_ids(isolated_store):
    store = isolated_store
    _seed(store, "chat-parent", [{"role": "user", "content": "parent"}])
    _seed(
        store, "chat-child", [{"role": "user", "content": "subagent worker"}],
        conv_type="subagent", parent_id="chat-parent",
    )

    assert history.read_conversation_transcript(
        "chat-parent", exclude_conversation_id="chat-parent",
    ) is None
    assert history.read_conversation_transcript(
        "chat-child", exclude_conversation_id="chat-other",
    ) is None
    assert history.read_conversation_transcript("missing") is None
    assert history.read_conversation_transcript("../../etc/passwd") is None


# ---------------------------------------------------------------------------
# Metadata-only list pagination
# ---------------------------------------------------------------------------

def test_list_is_metadata_only_paginated_and_scoped(isolated_store):
    store = isolated_store
    for cid, text in [
        ("chat-a", "alpha"),
        ("chat-b", "bravo"),
        ("chat-c", "charlie"),
    ]:
        time.sleep(0.002)
        _seed(store, cid, [{"role": "user", "content": text}])
    _seed(
        store, "chat-sub", [{"role": "user", "content": "hidden worker"}],
        conv_type="subagent", parent_id="chat-a",
    )

    page1 = history.list_conversation_metadata(
        exclude_conversation_id="chat-current", limit=2,
    )
    assert [c["conversation_id"] for c in page1["conversations"]] == ["chat-c", "chat-b"]
    assert set(page1["conversations"][0]) == {
        "conversation_id", "title", "created_at", "updated_at",
    }

    # Creating a newer chat must not shift the already-issued cursor.
    time.sleep(0.002)
    _seed(store, "chat-new", [{"role": "user", "content": "newest"}])

    page2 = history.list_conversation_metadata(
        exclude_conversation_id="chat-current", limit=2, cursor=page1["next_cursor"],
    )
    assert [c["conversation_id"] for c in page2["conversations"]] == ["chat-a"]
    assert page2["next_cursor"] is None

    scoped = history.list_conversation_metadata(exclude_conversation_id="chat-a", limit=10)
    ids = {c["conversation_id"] for c in scoped["conversations"]}
    assert "chat-a" not in ids and "chat-sub" not in ids

    with pytest.raises(ValueError):
        history.list_conversation_metadata("chat-current", cursor="not-a-cursor")


# ---------------------------------------------------------------------------
# Multilingual full-transcript search
# ---------------------------------------------------------------------------

def test_search_finds_chinese_and_english_deep_in_old_transcripts(isolated_store):
    store = isolated_store
    old_target = "chat-old-decisions"
    long_opener = "Opening onboarding notes that never mention the topic. " * 150
    _seed(store, old_target, [
        {"role": "user", "content": long_opener},
        {"role": "assistant", "content": "Noted."},
        {"role": "user", "content": "Reminder: 请把数据库迁移安排在周五。 The deployment owner is 李雷."},
        {"role": "assistant", "content": "Decided: the deployment owner is 李雷 and 数据库迁移 ships Friday."},
        {"role": "tool", "tool_call_id": "c1", "content": "TOOL_ONLY_SECRET"},
        {
            "role": "assistant", "content": "", "reasoning_content": "REASONING_ONLY_SECRET",
            "tool_calls": [{"id": "c2", "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"}}],
        },
        {"role": "assistant", "content": "Final: 就按这个计划执行。"},
    ])
    _seed(
        store, "chat-sub-secret", [{"role": "user", "content": "subagent private transcript"}],
        conv_type="subagent", parent_id=old_target,
    )

    # Push the target far outside any "recent 30" window.
    for i in range(31):
        time.sleep(0.001)
        _seed(store, f"chat-filler-{i}", [{"role": "user", "content": f"unrelated chatter {i}"}])

    english = conversation_search.search_conversation_transcripts("deployment owner", limit=5)
    assert [r["conversation_id"] for r in english] == [old_target]
    snippets = english[0]["snippets"]
    assert snippets and all(s["role"] in {"user", "assistant"} for s in snippets)
    joined = " ".join(s["text"] for s in snippets)
    assert "deployment owner is 李雷" in joined
    # Snippet is taken next to the match, not from the opening segment.
    assert "Opening onboarding notes" not in joined

    chinese = conversation_search.search_conversation_transcripts("数据库迁移", limit=5)
    assert [r["conversation_id"] for r in chinese] == [old_target]
    assert any("数据库迁移" in s["text"] for s in chinese[0]["snippets"])

    # Private transcript material is not searchable and children are excluded.
    assert conversation_search.search_conversation_transcripts("TOOL_ONLY_SECRET") == []
    assert conversation_search.search_conversation_transcripts("REASONING_ONLY_SECRET") == []
    assert conversation_search.search_conversation_transcripts("subagent private") == []
    assert conversation_search.search_conversation_transcripts(
        "deployment owner", exclude_conversation_id=old_target,
    ) == []


def test_final_assistant_text_and_search_snippet_positions(isolated_store):
    """Normal turns keep their final answer; snippet positions stay raw indices.

    Later assistant text in a turn supersedes earlier drafts, a tool round's own
    prose never qualifies, and a snippet's ``position`` is the stored index of
    the message it was taken from, so it can drive read pagination directly.
    """
    store = isolated_store
    decision = "Final decision: the migration ships on Friday."
    long_context = "Supporting detail gathered earlier. " * 12
    _seed(store, "chat-decided", [
        {"role": "user", "content": "When does the migration ship?"},
        _tool_round("c1", content="Let me check the plan first."),
        {"role": "tool", "tool_call_id": "c1", "content": "TOOL output"},
        {"role": "assistant", "content": "Draft answer that was superseded."},
        {"role": "assistant", "content": long_context + decision},
        {"role": "user", "content": "And the rollback drill?"},
        _tool_round("c2", content="Checking the drill schedule."),
        {"role": "tool", "tool_call_id": "c2", "content": "TOOL output"},
    ])

    page = history.read_conversation_transcript("chat-decided", limit=10)
    assert [(m["role"], m["position"]) for m in page["messages"]] == [
        ("user", 0), ("assistant", 4), ("user", 5),
    ]
    assert page["messages"][1]["text"] == (long_context + decision).strip()

    # A broader query hits the user line too; snippets keep transcript order
    # and every position lines up with the visible message it came from.
    broad = conversation_search.search_conversation_transcripts("migration")
    assert [r["conversation_id"] for r in broad] == ["chat-decided"]
    assert [s["position"] for s in broad[0]["snippets"]] == [0, 4]
    assert [s["position"] for s in broad[0]["snippets"]] == [
        m["position"] for m in page["messages"] if "migration" in m["text"].lower()
    ]

    hits = conversation_search.search_conversation_transcripts("ships Friday")
    assert [r["conversation_id"] for r in hits] == ["chat-decided"]
    snippets = hits[0]["snippets"]
    assert [s["position"] for s in snippets] == [4]
    assert snippets[0]["text"].startswith("…")
    assert snippets[0]["text"].endswith(decision)

    # That position is a usable read cursor: it lands exactly after the match.
    tail = history.read_conversation_transcript(
        "chat-decided", after=snippets[0]["position"], limit=10,
    )
    assert [(m["role"], m["text"]) for m in tail["messages"]] == [
        ("user", "And the rollback drill?"),
    ]


# ---------------------------------------------------------------------------
# Gateway routes: auth is the per-turn credential, never the login token
# ---------------------------------------------------------------------------

@pytest.fixture
def password_protected_gateway(monkeypatch):
    import gateway.api as api
    monkeypatch.setattr(api, "AUTH_PASSWORD", "hunter2")
    return api


def test_history_routes_accept_only_live_turn_credentials(
    isolated_store, password_protected_gateway,
):
    store = isolated_store
    _seed(store, "chat-past", [{"role": "user", "content": "deploy on Fridays"}])
    _seed(store, "chat-current", [{"role": "user", "content": "current chat"}])

    client = TestClient(password_protected_gateway.app)
    login = client.post("/api/auth/login", json={"password": "hunter2"})
    assert login.status_code == 200
    user_token = login.json()["token"]

    # The ordinary user API still requires the login token.
    assert client.get("/api/conversations").status_code == 401
    assert client.get(
        "/api/conversations", headers={"Authorization": f"Bearer {user_token}"},
    ).status_code == 200

    # The internal history routes reject no-credential and the user token,
    # and the exemption is exact-path only (no general bypass).
    assert client.get("/api/conversation-history/list").status_code == 401
    assert client.get("/api/conversation-history/other").status_code == 401
    assert client.get(
        "/api/conversation-history/list",
        headers={"Authorization": f"Bearer {user_token}"},
    ).status_code == 401

    token = history.issue_turn_credential("chat-current")
    headers = {history.TURN_TOKEN_HEADER: token}

    listed = client.get("/api/conversation-history/list", headers=headers)
    assert listed.status_code == 200
    ids = [c["conversation_id"] for c in listed.json()["conversations"]]
    assert "chat-past" in ids and "chat-current" not in ids

    searched = client.get(
        "/api/conversation-history/search",
        params={"query": "deploy"},
        headers=headers,
    )
    assert searched.status_code == 200
    assert [r["conversation_id"] for r in searched.json()["results"]] == ["chat-past"]

    read = client.get(
        "/api/conversation-history/read",
        params={"conversation_id": "chat-past"},
        headers=headers,
    )
    assert read.status_code == 200
    assert read.json()["messages"][0]["text"] == "deploy on Fridays"

    # The current chat is not readable even with a valid credential.
    assert client.get(
        "/api/conversation-history/read",
        params={"conversation_id": "chat-current"},
        headers=headers,
    ).status_code == 404
    assert client.get(
        "/api/conversation-history/list", params={"cursor": "???"}, headers=headers,
    ).status_code == 400

    history.revoke_turn_credential(token)
    assert client.get("/api/conversation-history/list", headers=headers).status_code == 401

    # Expired credentials die on their own.
    expired = history.issue_turn_credential("chat-current", ttl=-1)
    assert client.get(
        "/api/conversation-history/list",
        headers={history.TURN_TOKEN_HEADER: expired},
    ).status_code == 401


def test_proxy_chat_mints_fresh_credential_and_drops_client_supplied(
    isolated_store, monkeypatch,
):
    """The gateway, not the frontend, decides the credential for a turn."""
    import gateway.api as api
    import gateway.routes as routes

    monkeypatch.setattr(api, "AUTH_PASSWORD", "")
    monkeypatch.setattr(routes, "store", isolated_store)
    captured = []

    async def fake_proxy(stream, body):
        captured.append(dict(body))
        stream.finished = True

    async def fake_sse(stream, queue, request, replay_latest=False):
        yield "data: {}\n\n"

    monkeypatch.setattr(routes, "_proxy_backend_stream", fake_proxy)
    monkeypatch.setattr(routes, "_subscriber_sse", fake_sse)
    client = TestClient(api.app)

    response = client.post("/api/chat", json={
        "message": "what did we decide?",
        "conversation_id": "chat-current",
        # A malicious/buggy client must not be able to choose its credential.
        "conversation_history_token": "attacker-supplied",
    })
    assert response.status_code == 200
    token = captured[0]["conversation_history_token"]
    assert token != "attacker-supplied"
    assert history.resolve_turn_credential(token) is None
    assert history.resolve_turn_credential("attacker-supplied") is None

    # The credential rides only in the backend request, never the transcript.
    stored = json.dumps(isolated_store.get_messages("chat-current"))
    assert "conversation_history_token" not in stored and token not in stored

    # Subagent turns get no cross-conversation credential at all.
    from gateway import streaming
    worker = client.post("/api/chat", json={
        "message": "worker task",
        "conv_type": "subagent",
        "parent_id": "chat-current",
    })
    try:
        assert "conversation_history_token" not in captured[1]
    finally:
        for conv_id in ("chat-current", worker.headers.get("x-conversation-id")):
            streaming.active_streams.pop(conv_id, None)


# ---------------------------------------------------------------------------
# Backend tool client — real gateway routes, real credential
# ---------------------------------------------------------------------------

def _wire_tools_to_gateway(monkeypatch, api, expected_token):
    client = TestClient(api.app)

    def fake_get(url, params=None, headers=None, timeout=None, allow_redirects=True):
        assert headers[history.TURN_TOKEN_HEADER] == expected_token
        # The turn credential is scoped to the gateway we addressed, so the
        # bridge must never let requests follow a redirect with it attached.
        assert allow_redirects is False
        path = url.split("http://gateway", 1)[1]
        return client.get(path, params=params, headers=headers)

    monkeypatch.setattr(tools_client.requests, "get", fake_get)
    monkeypatch.setattr(tools_client, "GATEWAY_URL", "http://gateway")
    return client


def test_tool_client_hits_real_gateway_routes(isolated_store, monkeypatch):
    store = isolated_store
    _seed(store, "chat-past", [
        {"role": "user", "content": "We decided the 部署 owner is Alice."},
        {"role": "assistant", "content": "Confirmed, Alice owns 部署."},
    ])
    import gateway.api as api

    token = history.issue_turn_credential("chat-current")
    _wire_tools_to_gateway(monkeypatch, api, token)

    bound = tools_client.set_turn_token(token)
    try:
        listed, _ = tools_client.list_conversations_tool({})
        assert "chat-past" in listed and "chat-current" not in listed

        found, _ = tools_client.search_conversations_tool({"query": "部署"})
        assert "chat-past" in found and "Alice owns 部署" in found

        read, _ = tools_client.read_conversation_tool({"conversation_id": "chat-past"})
        assert "We decided the 部署 owner is Alice." in read
        assert "Confirmed, Alice owns 部署." in read
    finally:
        tools_client.reset_turn_token(bound)

    # No credential bound (eg. subagent/worker turn) -> graceful, not bypass.
    unbound, _ = tools_client.list_conversations_tool({})
    assert "unavailable" in unbound.lower()


def test_main_flow_tool_round_trip_uses_gateway_credential(isolated_store, monkeypatch):
    store = isolated_store
    _seed(store, "chat-past", [
        {"role": "user", "content": "Earlier we agreed: 部署 owner is Alice."},
    ])
    import gateway.api as api

    token = history.issue_turn_credential("chat-current")
    _wire_tools_to_gateway(monkeypatch, api, token)

    tool_calls = [
        SimpleNamespace(
            index=0, id="call-list",
            function=SimpleNamespace(name="list_conversations", arguments="{}"),
        ),
        SimpleNamespace(
            index=1, id="call-search",
            function=SimpleNamespace(
                name="search_conversations", arguments=json.dumps({"query": "部署"}),
            ),
        ),
    ]
    responses = [
        SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="tool_calls",
            delta=SimpleNamespace(content=None, tool_calls=tool_calls),
        )]),
        SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop",
            delta=SimpleNamespace(content="Alice owns 部署.", tool_calls=None),
        )]),
    ]

    def create(**kwargs):
        return iter([responses.pop(0)])

    monkeypatch.setattr(main_flow.provider_manager, "get_client", lambda _: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    ))
    monkeypatch.setattr(main_flow.provider_manager, "get_config", lambda _: {"model": "fixture"})
    monkeypatch.setattr(main_flow.provider_manager, "get_model_limits", lambda *_: (0, 0))
    monkeypatch.setattr(main_flow, "prefetch_primary_tools", lambda: None)
    monkeypatch.setattr(main_flow, "record_api_call", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_flow, "get_panels", lambda: [])

    result = list(main_flow.generate_chat_responses_stream_native(
        [
            {"role": "system", "content": "fixture"},
            {"role": "user", "content": "What did we decide about 部署?"},
        ],
        max_iterations=3,
        provider_id="fixture",
        turn_token=token,
    ))

    assert result[-1]["status"] == "completed"
    tool_results = [m["content"] for m in result[-1]["messages"] if m["role"] == "tool"]
    assert len(tool_results) == 2, tool_results
    combined = "\n".join(tool_results)
    assert "chat-past" in combined
    assert "部署" in combined and "Alice" in combined
    # The turn-scoped binding is gone once the generator completes.
    assert tools_client.current_turn_token() is None


def test_backend_http_chat_forwards_turn_credential_to_tool(isolated_store, monkeypatch):
    """The backend's own HTTP boundary (ChatRequest -> agent loop) is wired."""
    store = isolated_store
    _seed(store, "chat-past", [
        {"role": "user", "content": "Earlier we agreed: 部署 owner is Alice."},
    ])
    import importlib

    import gateway.api as api
    backend_module = importlib.import_module("src.web_api.app")

    token = history.issue_turn_credential("chat-current")
    _wire_tools_to_gateway(monkeypatch, api, token)

    tool_call = SimpleNamespace(
        index=0, id="call-search",
        function=SimpleNamespace(
            name="search_conversations", arguments=json.dumps({"query": "部署"}),
        ),
    )
    responses = [
        SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="tool_calls",
            delta=SimpleNamespace(content=None, tool_calls=[tool_call]),
        )]),
        SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop",
            delta=SimpleNamespace(content="Alice owns 部署.", tool_calls=None),
        )]),
    ]

    def create(**kwargs):
        return iter([responses.pop(0)])

    monkeypatch.setattr(main_flow.provider_manager, "get_client", lambda _: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    ))
    monkeypatch.setattr(main_flow.provider_manager, "get_config", lambda _: {"model": "fixture"})
    monkeypatch.setattr(main_flow.provider_manager, "get_model_limits", lambda *_: (0, 0))
    monkeypatch.setattr(main_flow, "prefetch_primary_tools", lambda: None)
    monkeypatch.setattr(main_flow, "record_api_call", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_flow, "get_panels", lambda: [])

    client = TestClient(backend_module.app)
    response = client.post("/api/chat", json={
        "message": "What did we decide about 部署?",
        "conversation_id": "chat-current",
        "provider": "fixture",
        "max_iterations": 3,
        "conversation_history_token": token,
    })

    assert response.status_code == 200
    assert "chat-past" in response.text


# ---------------------------------------------------------------------------
# Credential lifecycle tied to the proxied turn
# ---------------------------------------------------------------------------







# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def test_history_tools_are_registered_even_when_memory_is_disabled(monkeypatch):
    import src.tool_definitions as tool_definitions

    monkeypatch.setattr(tool_definitions, "memory_enabled", lambda: False)
    names = {t["function"]["name"] for t in tool_definitions.get_tool_definitions()}
    assert {"list_conversations", "search_conversations", "read_conversation"} <= names
    assert "remember" not in names

    function_map = tool_definitions.get_tool_function_map()
    assert function_map["list_conversations"] is tools_client.list_conversations_tool
    assert function_map["search_conversations"] is tools_client.search_conversations_tool
    assert function_map["read_conversation"] is tools_client.read_conversation_tool
    # Subagents do not receive the history tools; they have no turn credential.
    assert "read_conversation" not in tool_definitions.SUBAGENT_READ_ONLY_TOOLS


# ---------------------------------------------------------------------------
# Derived visible-projection cache
# ---------------------------------------------------------------------------

def test_repeated_search_and_read_reuse_projection_without_reparsing(
    isolated_store, monkeypatch,
):
    """Follow-up searches/reads must not re-parse every stored transcript.

    The projection is exactly what a history read may return, so the marker
    assertions double as a redaction check for the cached copy.
    """
    store = isolated_store
    _seed(store, "chat-cache-en", [
        {"role": "user", "content": "Where is the deployment runbook?"},
        {"role": "assistant", "content": "The deployment runbook is docs/deploy.md."},
    ])
    _seed(store, "chat-cache-zh", [
        {"role": "user", "content": "数据库迁移安排在周五。"},
        {"role": "assistant", "content": "确认：数据库迁移在周五执行。"},
    ])
    _seed(store, "chat-cache-private", [
        {"role": "user", "content": "CACHE_VISIBLE question"},
        {"role": "tool", "tool_call_id": "c1", "content": "CACHE_TOOL_SECRET"},
        _tool_round("c2", content="CACHE_PROGRESS_SECRET"),
        {"role": "assistant", "content": "CACHE_VISIBLE answer"},
    ])
    _seed(
        store, "chat-cache-child",
        [{"role": "user", "content": "ZZCHILD9Q private"}],
        parent_id="chat-cache-en",
    )

    calls = {"snapshot": 0, "get": 0}
    original_snapshot = store.read_messages_snapshot
    original_get = store.get_messages

    def counting_snapshot(conversation_id):
        calls["snapshot"] += 1
        return original_snapshot(conversation_id)

    def counting_get(conversation_id):
        calls["get"] += 1
        return original_get(conversation_id)

    monkeypatch.setattr(store, "read_messages_snapshot", counting_snapshot)
    monkeypatch.setattr(store, "get_messages", counting_get)

    english = conversation_search.search_conversation_transcripts("deployment runbook")
    chinese = conversation_search.search_conversation_transcripts("数据库迁移")
    page = history.read_conversation_transcript("chat-cache-en", limit=10)
    # Only the three ordinary top-level chats are parsed, once each.
    assert calls == {"snapshot": 3, "get": 0}

    calls.update(snapshot=0, get=0)
    assert conversation_search.search_conversation_transcripts("deployment runbook") == english
    assert conversation_search.search_conversation_transcripts("数据库迁移") == chinese
    assert history.read_conversation_transcript("chat-cache-en", limit=10) == page
    assert calls == {"snapshot": 0, "get": 0}

    # Cached material is the visible projection only: no tool output, no
    # interrupted-round narration, and no child/subagent transcript.
    cached_text = " ".join(
        message["text"]
        for _, projected, _ in conversation_search._visible_cache.values()
        for message in projected
    )
    assert "CACHE_VISIBLE" in cached_text
    for hidden in (
        "CACHE_TOOL_SECRET", "CACHE_PROGRESS_SECRET", "ZZCHILD9Q",
    ):
        assert hidden not in cached_text

    # A warm cache cannot widen scope: exclusion still wins over cached hits.
    assert conversation_search.search_conversation_transcripts(
        "CACHE_VISIBLE", exclude_conversation_id="chat-cache-private",
    ) == []
    assert history.read_conversation_transcript(
        "chat-cache-en", exclude_conversation_id="chat-cache-en",
    ) is None
    assert conversation_search.search_conversation_transcripts(
        "ZZCHILD9Q",
    ) == []


def test_projection_cache_revalidates_appends_and_in_place_rewrites(isolated_store):
    """An edit must be visible on the next search/read, warm cache or not."""
    store = isolated_store
    conversation_id = "chat-rewrite"
    _seed(store, conversation_id, [
        {"role": "user", "content": "Old decision ALPHA123."},
        {"role": "assistant", "content": "Confirmed ALPHA123."},
    ])
    assert [r["conversation_id"] for r in
            conversation_search.search_conversation_transcripts("ALPHA123")] == [conversation_id]
    warmed = history.read_conversation_transcript(conversation_id, limit=10)
    assert [m["text"] for m in warmed["messages"]] == [
        "Old decision ALPHA123.", "Confirmed ALPHA123.",
    ]
    assert (str(store.storage_dir), conversation_id) in conversation_search._visible_cache

    # Normal append: new positions appear, old positions do not shift.
    store.save_messages(conversation_id, [
        {"role": "user", "content": "Old decision ALPHA123."},
        {"role": "assistant", "content": "Confirmed ALPHA123."},
        {"role": "user", "content": "New decision BETA456."},
        {"role": "assistant", "content": "Confirmed BETA456."},
    ])
    # The old messages are still present, so the old marker still matches.
    assert [r["conversation_id"] for r in
            conversation_search.search_conversation_transcripts("ALPHA123")] == [conversation_id]
    assert [r["conversation_id"] for r in
            conversation_search.search_conversation_transcripts("BETA456")] == [conversation_id]
    appended = history.read_conversation_transcript(conversation_id, limit=10)
    assert [m["position"] for m in appended["messages"]] == [0, 1, 2, 3]

    # Same-size rewrite with the old mtime restored: only content and ctime
    # change, so a size+mtime-only cache would keep serving the old answer.
    path = store._messages_path(conversation_id)
    old_stat = path.stat()
    rewriting = [
        {"role": "user", "content": "Rewritten decision GAMMA789."},
        {"role": "assistant", "content": "Confirmed GAMMA789."},
    ]
    payload = json.dumps(rewriting, ensure_ascii=False, indent=2).encode("utf-8")
    delta = old_stat.st_size - len(payload)
    assert delta >= 0
    rewriting[1]["content"] += "x" * delta
    payload = json.dumps(rewriting, ensure_ascii=False, indent=2).encode("utf-8")
    assert len(payload) == old_stat.st_size

    before = store.messages_signature(conversation_id)
    time.sleep(0.01)  # let the filesystem clock advance so ctime differs
    path.write_bytes(payload)
    os.utime(path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    after = store.messages_signature(conversation_id)
    assert after[:2] == before[:2]  # same inode and size
    assert after != before  # ctime (at least) reveals the rewrite

    assert conversation_search.search_conversation_transcripts("ALPHA123") == []
    assert conversation_search.search_conversation_transcripts("BETA456") == []
    assert [r["conversation_id"] for r in
            conversation_search.search_conversation_transcripts("GAMMA789")] == [conversation_id]
    rewritten = history.read_conversation_transcript(conversation_id, limit=10)
    assert [m["text"] for m in rewritten["messages"]] == [
        "Rewritten decision GAMMA789.",
        "Confirmed GAMMA789." + "x" * delta,
    ]


def test_projection_cache_never_replays_a_deleted_transcript(isolated_store):
    store = isolated_store
    conversation_id = "chat-vanished"
    _seed(store, conversation_id, [
        {"role": "user", "content": "ZEBRA72 was the plan."},
        {"role": "assistant", "content": "Confirmed ZEBRA72."},
    ])
    assert conversation_search.search_conversation_transcripts("ZEBRA72")
    assert history.read_conversation_transcript(
        conversation_id, limit=10,
    )["total_messages"] == 2

    store._messages_path(conversation_id).unlink()

    assert conversation_search.search_conversation_transcripts("ZEBRA72") == []
    gone = history.read_conversation_transcript(conversation_id, limit=10)
    assert gone is not None
    assert gone["messages"] == [] and gone["total_messages"] == 0
    assert (str(store.storage_dir), conversation_id) not in conversation_search._visible_cache

    # The index entry survives (this was not a routed delete), so listing keeps
    # working, but no cached text is replayed for the missing file.
    listed = history.list_conversation_metadata(exclude_conversation_id="chat-current")
    assert conversation_id in {c["conversation_id"] for c in listed["conversations"]}

    # Recreating the file is picked up; the deleted version stays gone.
    store.save_messages(conversation_id, [{"role": "user", "content": "RECREATED 5d1e"}])
    assert [r["conversation_id"] for r in
            conversation_search.search_conversation_transcripts("RECREATED 5d1e")] == [conversation_id]
    assert conversation_search.search_conversation_transcripts("ZEBRA72") == []


def test_projection_cache_is_bounded_and_evicted_entries_reload(
    isolated_store, monkeypatch,
):
    store = isolated_store
    monkeypatch.setattr(conversation_search, "VISIBLE_CACHE_MAX_CONVERSATIONS", 2)
    for i in range(3):
        _seed(store, f"chat-cap-{i}", [
            {"role": "user", "content": f"capacity marker CAPMARK{i}"},
            {"role": "assistant", "content": f"ack CAPMARK{i}"},
        ])

    for i in range(3):
        hits = conversation_search.search_conversation_transcripts(f"CAPMARK{i}")
        assert [r["conversation_id"] for r in hits] == [f"chat-cap-{i}"]
        assert len(conversation_search._visible_cache) <= 2

    # Eviction only drops derived data: the transcript reloads on demand.
    assert [r["conversation_id"] for r in
            conversation_search.search_conversation_transcripts("CAPMARK0")] == ["chat-cap-0"]
    assert len(conversation_search._visible_cache) <= 2
    assert conversation_search._visible_cache_chars == sum(
        entry[2] for entry in conversation_search._visible_cache.values()
    )


def test_cache_reads_preexisting_transcript_files(isolated_store):
    """Transcripts written by an older version need no cache artifacts."""
    store = isolated_store
    conversation_id = "chat-legacy-file"
    store.create_conversation(conversation_id=conversation_id, conv_type="user_chat")
    store._messages_path(conversation_id).write_text(json.dumps([
        {"role": "user", "content": "Legacy note: LEGACY77 deploy owner."},
        {"role": "assistant", "content": "Legacy answer LEGACY77."},
    ], ensure_ascii=False, indent=2), encoding="utf-8")

    assert [r["conversation_id"] for r in
            conversation_search.search_conversation_transcripts("LEGACY77")] == [conversation_id]
    page = history.read_conversation_transcript(conversation_id, limit=10)
    assert [m["text"] for m in page["messages"]] == [
        "Legacy note: LEGACY77 deploy owner.",
        "Legacy answer LEGACY77.",
    ]
