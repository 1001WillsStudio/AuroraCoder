"""Desktop conversation drawer: full-dialogue search over the user's history.

The drawer's search box only filtered the titles it had already loaded, so a
term that appears solely inside an old message could never be found.  This
route exposes the existing full-transcript search to the *logged-in user* —
the same visible projection the agent's tool uses — without excluding the chat
on screen, and projects the hits down to a bounded, drawer-sized payload.

Covered here: missing auth, the private agent token surface staying separate,
private agent transcripts never matching, bilingual (Chinese + English)
matching, payload bounds, and the scan not blocking other request handlers.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gateway import conversation_history as history
from gateway.conversation_store import ConversationStore
from gateway.conversation_history import (
    BROWSER_MAX_SNIPPETS_PER_CONVERSATION,
    BROWSER_MAX_SNIPPET_CHARS,
    BROWSER_SEARCH_DEFAULT_LIMIT,
    BROWSER_SEARCH_MAX_LIMIT,
)
from memory.ops import conversation_search

URL = "/api/conversation-browser/search"


def _seed(store, cid, messages, conv_type="user_chat", parent_id=None):
    store.create_conversation(
        conversation_id=cid, conv_type=conv_type, parent_id=parent_id,
    )
    store.save_messages(cid, messages)


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Isolated conversation store used by both the history and search modules."""
    store = ConversationStore(tmp_path / "history")
    monkeypatch.setattr(history, "store", store)
    monkeypatch.setattr(conversation_search, "store", store)
    conversation_search.clear_visible_transcript_cache()
    yield store
    conversation_search.clear_visible_transcript_cache()


@pytest.fixture
def gateway(monkeypatch):
    """Password-protected gateway, so the normal login middleware is active."""
    import gateway.api as api
    monkeypatch.setattr(api, "AUTH_PASSWORD", "hunter2")
    return api


def _login(client: TestClient) -> dict:
    token = client.post("/api/auth/login", json={"password": "hunter2"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Authentication: the ordinary user login middleware, not the agent credential
# ---------------------------------------------------------------------------

def test_search_requires_the_user_login_token(isolated_store, gateway):
    """An anonymous browser must not be able to search stored history."""
    _seed(isolated_store, "chat-1", [{"role": "user", "content": "deploy on Fridays"}])
    client = TestClient(gateway.app)

    assert client.get(URL, params={"query": "deploy"}).status_code == 401
    assert client.get(
        URL,
        params={"query": "deploy"},
        headers={"Authorization": "Bearer not-a-real-token"},
    ).status_code == 401

    response = client.get(URL, params={"query": "deploy"}, headers=_login(client))
    assert response.status_code == 200, response.text
    assert [r["conversation_id"] for r in response.json()["results"]] == ["chat-1"]


def test_private_agent_credential_cannot_open_the_user_search(
    isolated_store, gateway,
):
    """The per-turn agent token surface stays separate: it authenticates the
    internal ``/api/conversation-history/*`` reads only."""
    _seed(isolated_store, "chat-1", [{"role": "user", "content": "deploy notes"}])
    client = TestClient(gateway.app)

    turn_token = history.issue_turn_credential("chat-1")
    response = client.get(
        URL,
        params={"query": "deploy"},
        headers={history.TURN_TOKEN_HEADER: turn_token},
    )
    assert response.status_code == 401
    # The middleware exemption is exact-path only and must not grow this route.
    assert URL not in history.TURN_TOKEN_PATHS

    # The agent's own search still never sees the conversation that is asking.
    agent_search = client.get(
        "/api/conversation-history/search",
        params={"query": "deploy"},
        headers={history.TURN_TOKEN_HEADER: turn_token},
    )
    assert agent_search.status_code == 200
    assert agent_search.json()["results"] == []
    history.revoke_turn_credential(turn_token)


# ---------------------------------------------------------------------------
# The reported defect: matches inside old messages, not just titles
# ---------------------------------------------------------------------------

def test_finds_text_that_never_appears_in_the_title(isolated_store, gateway):
    """The drawer could only match titles; a body-only hit is the whole point."""
    store = isolated_store
    _seed(store, "chat-current", [
        {"role": "user", "content": "How do we cut over releases?"},
        {"role": "assistant", "content": "We run a blue-green switchover behind the "
                                        "load balancer, then drain the old pool."},
        {"role": "user", "content": "Thanks"},
        {"role": "assistant", "content": "Anytime."},
    ])
    client = TestClient(gateway.app)
    headers = _login(client)

    response = client.get(URL, params={"query": "blue-green switchover"}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["query"] == "blue-green switchover"

    results = body["results"]
    assert [r["conversation_id"] for r in results] == ["chat-current"]
    result = results[0]
    assert "blue-green" not in result["title"].lower(), "title cannot explain the hit"
    assert result["timestamp"], "a result row needs the conversation's last activity"
    assert result["snippets"] == [{
        "position": 1,
        "role": "assistant",
        "text": result["snippets"][0]["text"],
    }]
    assert "blue-green switchover" in result["snippets"][0]["text"]


def test_includes_the_conversation_currently_on_screen(isolated_store, gateway):
    """Unlike the agent-facing search, the drawer searches what the user can
    already read — the open chat must not be excluded."""
    store = isolated_store
    _seed(store, "chat-open", [
        {"role": "user", "content": "remind me about the rollback drill"},
        {"role": "assistant", "content": "It happens every quarter."},
    ])
    client = TestClient(gateway.app)

    results = client.get(
        URL, params={"query": "rollback drill"}, headers=_login(client),
    ).json()["results"]
    assert [r["conversation_id"] for r in results] == ["chat-open"]


def test_empty_query_returns_no_results(isolated_store, gateway):
    _seed(isolated_store, "chat-1", [{"role": "user", "content": "anything at all"}])
    client = TestClient(gateway.app)

    response = client.get(URL, params={"query": "   "}, headers=_login(client))
    assert response.status_code == 200
    assert response.json()["results"] == []


# ---------------------------------------------------------------------------
# Visibility: private agent working state is never user history
# ---------------------------------------------------------------------------

def test_private_agent_transcripts_never_match(isolated_store, gateway):
    store = isolated_store
    _seed(store, "chat-user", [{"role": "user", "content": "Where is the runbook?"}])
    # Subagent/worker transcript: private agent working state.
    _seed(store, "chat-subagent", [
        {"role": "user", "content": "runbook runbook runbook"},
        {"role": "assistant", "content": "the runbook is in docs/"},
    ], conv_type="subagent")
    # A child chat is private even when stored as a user_chat.
    _seed(store, "chat-child", [{"role": "user", "content": "runbook child"}],
          parent_id="chat-user")

    client = TestClient(gateway.app)
    results = client.get(
        URL, params={"query": "runbook"}, headers=_login(client),
    ).json()["results"]
    assert [r["conversation_id"] for r in results] == ["chat-user"]


def test_tool_output_reasoning_and_system_prompts_are_not_searchable(
    isolated_store, gateway,
):
    """Search must only reach the visible projection, never private internals."""
    store = isolated_store
    _seed(store, "chat-rich", [
        {"role": "system", "content": "SYSTEM SECRET deployment token"},
        {
            "role": "assistant", "content": "", "reasoning_content": "THINKING SECRET",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "run_terminal_command",
                                         "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "TOOL SECRET deployment"},
        {"role": "assistant", "content": "Draft about deployment that was retracted"},
        {"role": "assistant", "content": "The deployment runbook lives in docs/."},
    ])
    client = TestClient(gateway.app)
    headers = _login(client)

    for secret in ("SYSTEM SECRET", "THINKING SECRET", "TOOL SECRET",
                   "retracted"):
        body = client.get(URL, params={"query": secret}, headers=headers).json()
        assert body["results"] == [], f"{secret!r} must not be searchable"

    visible = client.get(URL, params={"query": "deployment"}, headers=headers).json()
    assert [r["conversation_id"] for r in visible["results"]] == ["chat-rich"]
    assert visible["results"][0]["snippets"][0]["position"] == 4


# ---------------------------------------------------------------------------
# Bilingual matching (the same matcher the agent tool uses)
# ---------------------------------------------------------------------------

def test_matches_chinese_and_english_deep_in_old_messages(isolated_store, gateway):
    store = isolated_store
    _seed(store, "chat-cn", [
        {"role": "user", "content": "线上部署是怎么做的？"},
        {"role": "assistant", "content": "先跑一遍冒烟测试，再灰度发布到生产环境。"},
        {"role": "user", "content": "收到"},
    ])
    _seed(store, "chat-en", [
        {"role": "user", "content": "how did the incident go?"},
        {"role": "assistant", "content": "The postmortem says we missed the alert."},
    ])
    client = TestClient(gateway.app)
    headers = _login(client)

    chinese = client.get(URL, params={"query": "灰度发布"}, headers=headers).json()
    assert [r["conversation_id"] for r in chinese["results"]] == ["chat-cn"]
    assert chinese["results"][0]["snippets"][0]["position"] == 1
    assert "灰度发布" in chinese["results"][0]["snippets"][0]["text"]

    english = client.get(URL, params={"query": "postmortem"}, headers=headers).json()
    assert [r["conversation_id"] for r in english["results"]] == ["chat-en"]
    assert "postmortem" in english["results"][0]["snippets"][0]["text"].lower()


# ---------------------------------------------------------------------------
# Payload shape: narrow fields, bounded snippets, clamped limits
# ---------------------------------------------------------------------------

def test_payload_is_narrow_and_bounded(isolated_store, gateway):
    store = isolated_store
    long_body = "x" * 300 + " needle " + "y" * 300
    _seed(store, "chat-long", [{"role": "user", "content": long_body}])
    _seed(store, "chat-many", [
        {"role": "user", "content": f"needle mention {i}"} for i in range(5)
    ])

    client = TestClient(gateway.app)
    response = client.get(URL, params={"query": "needle"}, headers=_login(client))
    assert response.status_code == 200
    by_id = {r["conversation_id"]: r for r in response.json()["results"]}
    assert set(by_id) == {"chat-long", "chat-many"}

    # Only what a drawer row needs: no score, no counts, no raw transcript.
    for result in by_id.values():
        assert set(result) == {"conversation_id", "title", "timestamp", "snippets"}
        assert len(result["snippets"]) <= BROWSER_MAX_SNIPPETS_PER_CONVERSATION
        for snippet in result["snippets"]:
            assert set(snippet) == {"position", "role", "text"}
            assert isinstance(snippet["position"], int)
            assert len(snippet["text"]) <= BROWSER_MAX_SNIPPET_CHARS

    # A multi-hundred-char message is clipped, never echoed back whole.
    clipped = by_id["chat-long"]["snippets"][0]
    assert len(clipped["text"]) == BROWSER_MAX_SNIPPET_CHARS
    assert clipped["text"].endswith("…")
    assert "needle" in clipped["text"]
    assert len(by_id["chat-many"]["snippets"]) == BROWSER_MAX_SNIPPETS_PER_CONVERSATION


def test_result_limit_is_bounded(isolated_store, gateway):
    store = isolated_store
    total = BROWSER_SEARCH_MAX_LIMIT + 5
    for index in range(total):
        _seed(store, f"chat-{index:02d}", [
            {"role": "user", "content": f"ticket triage note {index}"},
        ])
    client = TestClient(gateway.app)
    headers = _login(client)

    requests = client.get(URL, params={"query": "triage", "limit": 999},
                          headers=headers).json()["results"]
    assert len(requests) == BROWSER_SEARCH_MAX_LIMIT

    default_page = client.get(URL, params={"query": "triage"},
                              headers=headers).json()["results"]
    assert len(default_page) == BROWSER_SEARCH_DEFAULT_LIMIT


# ---------------------------------------------------------------------------
# The scan must not stall the event loop (and therefore other handlers)
# ---------------------------------------------------------------------------

def test_route_runs_the_scan_off_the_event_loop(monkeypatch):
    """A cold full-store scan is a blocking filesystem walk; the route must
    offload it, or one search stalls every other request in the gateway."""
    import asyncio
    import time

    import gateway.routes as routes

    def slow_search(query, limit=10):
        time.sleep(0.5)
        return {"query": query, "results": []}

    monkeypatch.setattr(routes, "search_conversation_history_for_browser", slow_search)

    async def scenario():
        task = asyncio.create_task(
            routes.conversation_browser_search(query="anything", limit=10)
        )
        # Give the task a chance to start.  If the scan ran inline, the loop
        # would be held for the whole sleep and the task would already be done.
        await asyncio.sleep(0.05)
        still_running = not task.done()
        return still_running, await task

    still_running, result = asyncio.run(scenario())
    assert still_running, "the transcript scan blocked the event loop"
    assert result == {"query": "anything", "results": []}
