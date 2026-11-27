from types import SimpleNamespace

import pytest

from src import main_flow
from src.message_provenance import TURN_INPUT_RECEIPT
from src.providers import messages_for_provider
from src.task_checklist import AgentTaskTools


def test_provider_replay_projects_old_transcript_fields_without_mutating_history():
    calls = [{
        "id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"},
    }]
    legacy = [
        {"role": "system", "content": "Instructions", "ts": "stored"},
        {"role": "user", "content": "Inspect the file", "attachedFiles": ["readme.md"]},
        {"role": "assistant", "content": "", "thinking": "Need the file", "tool_calls": calls},
        {"role": "tool", "content": "The file", "tool_call_id": "call_1", "name": "read_file", "ts": "stored"},
        {"role": "assistant", "content": "Done", "thinking": "Old alias", "reasoning_content": "Canonical"},
    ]

    assert messages_for_provider(legacy) == [
        {"role": "system", "content": "Instructions"},
        {"role": "user", "content": "Inspect the file"},
        {"role": "assistant", "content": "", "reasoning_content": "Need the file", "tool_calls": calls},
        {"role": "tool", "content": "The file", "tool_call_id": "call_1"},
        {"role": "assistant", "content": "Done", "reasoning_content": "Canonical"},
    ]
    assert legacy[2]["thinking"] == "Need the file"
    assert legacy[3]["ts"] == "stored"


def test_normal_provider_request_accepts_replay_and_records_the_actual_wire_messages(monkeypatch):
    original_assistant = {
        "role": "assistant", "content": "First half", "thinking": "UI reasoning", "reasoning_content": "Kept",
        "ts": "stored",
    }
    messages = [
        {"role": "system", "content": "Instructions"},
        {"role": "user", "content": "First request"},
        original_assistant,
        {"role": "user", "content": "Continue", "ts": "stored"},
    ]
    requests = []
    recorded = []

    def create(**kwargs):
        wire = kwargs["messages"]
        assert wire[2] == {"role": "assistant", "content": "First half", "reasoning_content": "Kept"}
        assert wire[3] == {"role": "user", "content": "Continue"}
        requests.append(wire)
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            content="Second half", tool_calls=None, reasoning_content="",
        ), finish_reason="stop")], usage=None)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(main_flow.provider_manager, "get_client", lambda _provider: client)
    monkeypatch.setattr(main_flow.provider_manager, "get_config", lambda _provider: {"model": "strict"})
    monkeypatch.setattr(main_flow.provider_manager, "get_model_limits", lambda *_: (4096, 1024))
    monkeypatch.setattr(main_flow, "prefetch_primary_tools", lambda: None)
    monkeypatch.setattr(main_flow, "record_api_call", lambda wire, _response, **_: recorded.append(wire))

    result = list(main_flow.generate_chat_responses_stream_native(
        messages, max_iterations=1, provider_id="opencode", tools_override=[
            {"type": "function", "function": {"name": "read_file"}},
        ],
    ))[-1]

    assert result["status"] == "completed"
    assert result["messages"][-1]["content"] == "Second half"
    assert result["messages"][2] == original_assistant
    assert recorded == requests


@pytest.mark.parametrize("finish_reason", ["stop", "tool_calls", "length"])
def test_followup_reaches_the_next_model_request_in_the_same_loop(monkeypatch, finish_reason):
    with_tools = finish_reason == "tool_calls"
    sent = []
    calls = [
        SimpleNamespace(index=index, id=f"call-{index}", function=SimpleNamespace(name="read_file", arguments="{}"))
        for index in range(2)
    ]

    def create(**kwargs):
        sent.append(kwargs["messages"])
        first = len(sent) == 1
        delta = SimpleNamespace(
            content=("" if with_tools else "First answer finished.") if first else "Used the new direction.",
            tool_calls=calls if first and with_tools else None,
        )
        reason = finish_reason if first else "stop"
        return iter([SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=reason)], usage=None)])

    def execute(batch, messages, **_kwargs):
        messages.extend(
            {"role": "tool", "tool_call_id": call["id"], "content": f"Result {call['id']}"}
            for call in batch
        )
        return {}

    def take(messages, *, finishing):
        if len(sent) != 1:
            assert finishing
            return False
        assert [message["role"] for message in messages] == (
            ["system", "user", "assistant", "tool", "tool"] if with_tools else ["system", "user", "assistant"]
        )
        messages.append({"role": "user", "content": "New direction", TURN_INPUT_RECEIPT: "fixture"})
        return True

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(main_flow.provider_manager, "get_client", lambda _: client)
    monkeypatch.setattr(main_flow.provider_manager, "get_config", lambda _: {"model": "fixture"})
    monkeypatch.setattr(main_flow.provider_manager, "get_model_limits", lambda *_: (4096, 1024))
    monkeypatch.setattr(main_flow, "prefetch_primary_tools", lambda: None)
    monkeypatch.setattr(main_flow, "get_tool_definitions", lambda: [])
    monkeypatch.setattr(main_flow, "load_save_training_flag", lambda: False)
    monkeypatch.setattr(main_flow, "execute_tool_calls", execute)
    task = AgentTaskTools("conversation", "lease", None)
    monkeypatch.setattr(task, "take_turn_inputs", take)
    messages = [{"role": "system", "content": "instructions"}, {"role": "user", "content": "First request"}]
    snapshots = list(main_flow.generate_chat_responses_stream_native(messages, max_iterations=1, task_tools=task))
    assert len(sent) == 2
    assert sent[1][-1] == {"role": "user", "content": "New direction"}
    if with_tools:
        assert [message["tool_call_id"] for message in sent[1][-3:-1]] == ["call-0", "call-1"]
    else:
        assert sent[1][-2]["content"] == "First answer finished."
    assert snapshots[-1]["status"] == "completed"
    assert snapshots[-1]["messages"][-1]["content"] == "Used the new direction."
