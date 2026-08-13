"""Regression: "Continue in new chat" must open a new thread, not post a tool prompt.

Explorer t-8a4230fa82: the control sent "Please use the `continue_as_new_chat`
tool…" as a user message on the same conversation. History did not grow.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PROMPT = "Please use the `continue_as_new_chat` tool"
_LEAKED = (
    "Please use the `continue_as_new_chat` tool to hand off this task "
    "to a new chat with fresh context."
)


@pytest.fixture(autouse=True)
def _silence_memory_ops(monkeypatch):
    monkeypatch.setattr(
        "gateway.streaming._memory_ops_executor.submit", lambda *a, **k: None,
    )


def _transcript():
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Please add a login page."},
        {
            "role": "assistant",
            "content": "Created frontend/src/Login.jsx.",
            "tool_calls": [{"id": "c1", "function": {"name": "write_file", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "wrote Login.jsx"},
    ]


def test_frontend_does_not_post_continue_tool_instruction_as_user_message():
    app_jsx = (_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    api_js = (_ROOT / "frontend" / "src" / "services" / "api.js").read_text(encoding="utf-8")
    assert _TOOL_PROMPT not in app_jsx
    assert "force_continuation" not in app_jsx
    assert "continueAsNewChat" in app_jsx
    assert "continue-as-new" in api_js


def test_handoff_prompt_summarizes_progress_without_tool_instruction():
    from gateway.streaming import build_user_continuation_prompt

    prompt = build_user_continuation_prompt(_transcript(), extra_note="Also add logout.")
    assert "Also add logout." in prompt
    assert "login page" in prompt.lower()
    assert "Login.jsx" in prompt and "write_file" in prompt
    assert _TOOL_PROMPT not in prompt and "`continue_as_new_chat`" not in prompt

    leaked = build_user_continuation_prompt(_transcript() + [{"role": "user", "content": _LEAKED}])
    assert _TOOL_PROMPT not in leaked
    assert "login page" in leaked.lower()


def test_continue_as_new_endpoint_hands_off_without_touching_source(monkeypatch):
    from fastapi.testclient import TestClient
    from gateway.api import app
    from gateway.conversation_store import store

    started = []

    async def _fake_start(new_cid, provider_id, user_msg):
        started.append((new_cid, user_msg))

    monkeypatch.setattr("gateway.routes._start_continuation", _fake_start)

    source_id = store.create_conversation(conv_type="user_chat", provider_id="deepseek")
    original = _transcript()
    store.save_messages(source_id, original)
    store.save_frontend_messages(source_id, [{"role": "user", "content": "Please add a login page."}])
    before_ids = {c["id"] for c in store.list_conversations()}

    res = TestClient(app).post(
        f"/api/conversations/{source_id}/continue-as-new",
        json={"message": "Also add logout."},
    )
    assert res.status_code == 200, res.text
    new_cid = res.json()["new_conversation_id"]
    after_ids = {c["id"] for c in store.list_conversations()}

    assert new_cid not in before_ids and new_cid in after_ids
    assert len(after_ids) == len(before_ids) + 1
    assert store.get_conversation(source_id)["status"] == "continued"
    assert store.get_messages(source_id) == original
    assert store.get_conversation(new_cid).get("parent_id") is None
    new_content = store.get_messages(new_cid)[0]["content"]
    assert "[Continued from previous agent session]" in new_content
    assert "Also add logout." in new_content and "login page" in new_content.lower()
    assert _TOOL_PROMPT not in new_content
    assert all(_TOOL_PROMPT not in (m.get("content") or "") for m in original)
    assert started and started[0][0] == new_cid and _TOOL_PROMPT not in started[0][1]


def test_continue_as_new_endpoint_404_for_unknown_conversation(monkeypatch):
    from fastapi.testclient import TestClient
    from gateway.api import app

    async def _fake_start(*a, **k):
        raise AssertionError("must not start a continuation for a missing source")

    monkeypatch.setattr("gateway.routes._start_continuation", _fake_start)
    res = TestClient(app).post(
        "/api/conversations/00000000-0000-0000-0000-000000000000/continue-as-new",
        json={},
    )
    assert res.status_code == 404
