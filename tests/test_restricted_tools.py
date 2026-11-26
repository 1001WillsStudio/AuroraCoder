from types import SimpleNamespace

from src import main_flow, tool_executor


def tool_call(call_id, name):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}


def test_explicit_toolset_denies_unavailable_calls_and_replies_in_order(monkeypatch):
    ran = []
    monkeypatch.setattr(tool_executor, "execute_tool_call", lambda name, args, **_: (args, ran.append(name) or "ok"))
    calls = [
        tool_call("1", "read_file"),
        tool_call("2", "edit_file"),
        tool_call("3", "run_terminal_command"),
        tool_call("4", "tool_store"),
        tool_call("5", "read_file"),
    ]
    messages = []
    tool_executor.execute_tool_calls(calls, messages, allowed_tools={"read_file"})

    assert ran == ["read_file", "read_file"]
    assert [msg["tool_call_id"] for msg in messages] == ["1", "2", "3", "4", "5"]
    assert [msg["content"] for msg in messages[1:4]] == [
        f"Error: tool '{name}' is not available in this session."
        for name in ("edit_file", "run_terminal_command", "tool_store")
    ]


def test_empty_tool_override_stays_empty_and_denies_unavailable_handoff(monkeypatch):
    requests = []
    ran = []

    def create(**kwargs):
        requests.append(kwargs.copy())
        call = SimpleNamespace(
            index=0, id="call1",
            function=SimpleNamespace(name="continue_as_new_chat", arguments="{}"),
        )
        delta = SimpleNamespace(content="" if len(requests) == 1 else "Finished",
                                tool_calls=[call] if len(requests) == 1 else None)
        reason = "tool_calls" if delta.tool_calls else "stop"
        return iter([SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=reason)], usage=None)])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(main_flow.provider_manager, "get_client", lambda _provider: client)
    monkeypatch.setattr(main_flow.provider_manager, "get_config", lambda _provider: {})
    monkeypatch.setattr(main_flow.provider_manager, "get_model_limits", lambda *_: (4096, 1024))
    monkeypatch.setattr(main_flow, "prefetch_primary_tools", lambda: None)
    monkeypatch.setattr(main_flow, "load_save_training_flag", lambda: False)
    monkeypatch.setattr(tool_executor, "execute_tool_call", lambda name, args, **_: (args, ran.append(name)))

    final = list(main_flow.generate_chat_responses_stream_native(
        [{"role": "system", "content": "Inspect."}, {"role": "user", "content": "Can you inspect this?"}],
        tools_override=[], max_iterations=2,
    ))[-1]

    assert len(requests) == 2
    assert all("tools" not in request and "tool_choice" not in request for request in requests)
    assert ran == []
    assert final["status"] == "completed"
    assert final["messages"][-1]["content"] == "Finished"
    assert final["messages"][-2]["content"] == "Error: tool 'continue_as_new_chat' is not available in this session."
