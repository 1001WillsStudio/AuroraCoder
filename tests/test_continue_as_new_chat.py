"""Regression: "Continue in new chat" must be summarized by the current agent.

Explorer t-8a4230fa82: the control sent "Please use the `continue_as_new_chat`
tool…" as a user message on the same conversation. History did not grow.
The handoff brief must come from the current agent (tool prompt, or its
text reply on a forced continuation turn) — not a gateway transcript dump.
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
_AGENT_SUMMARY = (
    "Login page is in frontend/src/Login.jsx. Still need logout and tests."
)


@pytest.fixture
def _silence_memory_ops(monkeypatch):
    monkeypatch.setattr(
        "gateway.streaming._memory_ops_executor.submit", lambda *a, **k: None,
    )


def _transcript(*, with_tool=False, assistant_text=_AGENT_SUMMARY):
    assistant = {
        "role": "assistant",
        "content": assistant_text,
    }
    if with_tool:
        assistant["tool_calls"] = [{
            "id": "c2",
            "function": {
                "name": "continue_as_new_chat",
                "arguments": '{"prompt": "%s"}' % _AGENT_SUMMARY,
            },
        }]
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Please add a login page."},
        {
            "role": "assistant",
            "content": "Created frontend/src/Login.jsx.",
            "tool_calls": [{"id": "c1", "function": {"name": "write_file", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "wrote Login.jsx"},
        assistant,
    ]


def test_frontend_asks_current_agent_without_posting_tool_instruction():
    app_jsx = (_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    api_js = (_ROOT / "frontend" / "src" / "services" / "api.js").read_text(encoding="utf-8")
    assert _TOOL_PROMPT not in app_jsx
    assert "continueAsNewChat" not in app_jsx
    assert "continue-as-new" not in api_js
    assert "handleContinueInNewChat" in app_jsx
    assert "force_continuation" in app_jsx
    assert "handleSend(null, combinedMessage" not in app_jsx


def test_force_continuation_unlocks_only_continue_tool_and_injects_system_instruction():
    from src.web_api.app import (
        FORCE_CONTINUATION_INSTRUCTION,
        apply_force_continuation_instruction,
        convert_messages_for_frontend,
        get_filtered_tools,
    )

    names = [t["function"]["name"] for t in get_filtered_tools("force_continuation")]
    assert names == ["continue_as_new_chat"]

    msgs = [{"role": "user", "content": "Please add a login page."}]
    apply_force_continuation_instruction(msgs)
    assert msgs[-1]["role"] == "system"
    assert "Call continue_as_new_chat now" in msgs[-1]["content"]
    assert FORCE_CONTINUATION_INSTRUCTION == msgs[-1]["content"]
    assert _TOOL_PROMPT not in msgs[-1]["content"]

    leaked = convert_messages_for_frontend(
        msgs + [{"role": "user", "content": _LEAKED}],
    )
    assert all(_TOOL_PROMPT not in (m.get("content") or "") for m in leaked)
    assert any(m.get("content") == "Please add a login page." for m in leaked)


def test_tool_choice_forced_when_continue_is_the_only_tool():
    from src.main_flow import _tool_choice_for

    only = [{"function": {"name": "continue_as_new_chat"}}]
    assert _tool_choice_for(only) == {
        "type": "function",
        "function": {"name": "continue_as_new_chat"},
    }
    assert _tool_choice_for([
        {"function": {"name": "read_file"}},
        {"function": {"name": "continue_as_new_chat"}},
    ]) == "auto"


def test_handoff_prompt_comes_from_current_agent_not_transcript_dump():
    from gateway.streaming import continuation_prompt_from_turn

    tool_turn = _transcript(with_tool=True)
    assert continuation_prompt_from_turn(tool_turn) == _AGENT_SUMMARY

    text_only = _transcript(with_tool=False)
    assert continuation_prompt_from_turn(text_only) is None
    assert continuation_prompt_from_turn(
        text_only, tools_mode="force_continuation", status="completed",
    ) == _AGENT_SUMMARY
    assert continuation_prompt_from_turn(
        text_only, tools_mode="force_continuation", status="running",
    ) is None

    leaked_reply = _transcript(with_tool=False, assistant_text=_LEAKED)
    assert continuation_prompt_from_turn(
        leaked_reply, tools_mode="force_continuation", status="completed",
    ) is None

    dump = "\n".join(
        f"{m['role']}: {m.get('content')}" for m in tool_turn if m.get("content")
    )
    prompt = continuation_prompt_from_turn(tool_turn)
    assert "wrote Login.jsx" not in prompt
    assert dump != prompt


def test_agent_tool_handoff_creates_new_chat_without_leaking_instruction(_silence_memory_ops):
    from gateway.conversation_store import store
    from gateway.streaming import handoff_to_new_conversation

    source_id = store.create_conversation(conv_type="user_chat", provider_id="deepseek")
    original = _transcript(with_tool=True)
    store.save_messages(source_id, original)
    store.save_frontend_messages(source_id, [
        {"role": "user", "content": "Please add a login page."},
    ])
    before_ids = {c["id"] for c in store.list_conversations()}

    new_cid, user_msg = handoff_to_new_conversation(
        source_cid=source_id,
        prompt=_AGENT_SUMMARY,
        provider_id="deepseek",
        source_raw_messages=original,
    )
    after_ids = {c["id"] for c in store.list_conversations()}

    assert new_cid not in before_ids and new_cid in after_ids
    assert len(after_ids) == len(before_ids) + 1
    assert store.get_conversation(source_id)["status"] == "continued"
    assert store.get_messages(source_id) == original
    assert _AGENT_SUMMARY in user_msg
    assert _TOOL_PROMPT not in user_msg
    assert all(_TOOL_PROMPT not in (m.get("content") or "") for m in original)
    assert "[Continued from previous agent session]" in store.get_messages(new_cid)[0]["content"]
