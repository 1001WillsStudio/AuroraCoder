import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from types import SimpleNamespace

import pytest

from src import main_flow, tool_executor

backend = import_module("src.web_api.app")


@pytest.mark.asyncio
async def test_stopping_turn_prevents_later_tool_calls_in_the_same_batch(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    ran = []

    def execute(name, args, **_):
        assert name == "run_terminal_command"
        command = args["command"]
        ran.append(command)
        if command == "first":
            started.set()
            assert release.wait(5)
        return args, "Finished " + command

    def create(**_kwargs):
        calls = [
            SimpleNamespace(index=index, id="call-" + command, function=SimpleNamespace(
                name="run_terminal_command", arguments=json.dumps({"command": command}),
            ))
            for index, command in enumerate(("first", "second"))
        ]
        delta = SimpleNamespace(content="", tool_calls=calls)
        return iter([SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(main_flow.provider_manager, "get_client", lambda _provider: client)
    monkeypatch.setattr(main_flow.provider_manager, "get_config", lambda _provider: {})
    monkeypatch.setattr(main_flow.provider_manager, "get_model_limits", lambda *_: (4096, 1024))
    monkeypatch.setattr(main_flow, "prefetch_primary_tools", lambda: None)
    monkeypatch.setattr(main_flow, "load_save_training_flag", lambda: False)
    monkeypatch.setattr(tool_executor, "execute_tool_call", execute)
    monkeypatch.setattr(backend, "cancel_active_subagents", lambda *_: None)

    class ConnectedRequest:
        async def is_disconnected(self):
            return False

    async def consume():
        states = backend.stream_chat_response(
            [{"role": "system", "content": "Test."}, {"role": "user", "content": "Run both tools."}],
            "stop-fixture", ConnectedRequest(), max_iterations=1,
        )
        return [event async for event in states]

    task = asyncio.create_task(consume())
    try:
        assert await asyncio.to_thread(started.wait, 5)
        assert backend.cancel_active_stream("stop-fixture")
    finally:
        release.set()
        await asyncio.wait_for(task, 5)

    assert ran == ["first"]


def test_stopping_before_parallel_worker_enters_preserves_only_the_completed_result(monkeypatch):
    stopped = threading.Event()
    calls = [
        {"id": f"call-{n}", "function": {"name": "read_file", "arguments": json.dumps({"file": n})}}
        for n in ("first", "queued")
    ]
    ran = []

    def execute(_, args, **_options):
        ran.append(args["file"])
        stopped.set()
        return args, "Finished first"

    monkeypatch.setattr(tool_executor, "execute_tool_call", execute)
    messages = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        monkeypatch.setattr(tool_executor, "_get_executor", lambda: executor)
        tool_executor.execute_tool_calls(calls, messages, stop_requested=stopped.is_set)

    assert ran == ["first"]
    assert messages == [{"role": "tool", "tool_call_id": "call-first", "content": "Finished first"}]
