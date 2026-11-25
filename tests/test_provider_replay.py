from types import SimpleNamespace

from src import main_flow
from src.providers import messages_for_provider


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
        ))], usage=None)

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
