import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from importlib import import_module
from types import SimpleNamespace

import httpx
import pytest

from gateway import routes, streaming
from gateway.conversation_store import ConversationStore
from src.backend_access import backend_auth_headers
from src import main_flow, tool_executor

backend = import_module("src.web_api.app")


def _stub_model(monkeypatch, create):
    completions = SimpleNamespace(create=create)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(main_flow.provider_manager, "get_client", lambda _: client)
    monkeypatch.setattr(main_flow.provider_manager, "get_config", lambda _: {})
    monkeypatch.setattr(main_flow.provider_manager, "get_model_limits", lambda *_: (4096, 1024))
    monkeypatch.setattr(main_flow, "prefetch_primary_tools", lambda: None)
    monkeypatch.setattr(main_flow, "load_save_training_flag", lambda: False)


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
        return iter([SimpleNamespace(
            choices=[SimpleNamespace(delta=delta, finish_reason="tool_calls")], usage=None,
        )])

    _stub_model(monkeypatch, create)
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


def test_stop_releases_the_caller_while_parallel_tools_are_still_blocked(monkeypatch):
    stopped = threading.Event()
    release = threading.Event()
    entered = threading.Barrier(3)
    calls = [
        {"id": name, "function": {"name": "read_file", "arguments": "{}"}}
        for name in ("first", "second")
    ]

    def execute(_, args, **_options):
        entered.wait(5)
        assert release.wait(5)
        return args, "Finished"

    monkeypatch.setattr(tool_executor, "execute_tool_call", execute)
    messages = []
    with ThreadPoolExecutor(max_workers=2) as workers, ThreadPoolExecutor(max_workers=1) as turn:
        monkeypatch.setattr(tool_executor, "_get_executor", lambda: workers)
        pending = turn.submit(
            tool_executor.execute_tool_calls, calls, messages, stop_requested=stopped.is_set,
        )
        try:
            entered.wait(5)
            stopped.set()
            assert pending.result(timeout=1) == {}
            assert not messages
        finally:
            release.set()


@pytest.mark.asyncio
async def test_gateway_stop_persists_completed_parallel_result(monkeypatch, tmp_path):
    def create(**_kwargs):
        calls = [
            SimpleNamespace(
                index=index, id=name,
                function=SimpleNamespace(name="read_file", arguments=json.dumps({"file": name})),
            )
            for index, name in enumerate(("first", "second"))
        ]
        delta = SimpleNamespace(content="", tool_calls=calls)
        choice = SimpleNamespace(delta=delta, finish_reason="tool_calls")
        return iter([SimpleNamespace(choices=[choice], usage=None)])

    _stub_model(monkeypatch, create)
    store = ConversationStore(tmp_path)
    cid = store.create_conversation()
    stream = streaming.ActiveStream(conversation_id=cid)
    monkeypatch.setenv("ACCESS_PASSWORD", "stop fixture")
    monkeypatch.setattr(streaming, "store", store)
    monkeypatch.setattr(routes, "store", store)
    owners = {cid: stream}
    monkeypatch.setattr(routes, "active_streams", owners)
    monkeypatch.setattr(streaming, "active_streams", owners)
    monkeypatch.setattr(streaming, "_schedule_memory_distillation", lambda *_: None)
    monkeypatch.setattr(backend, "cancel_active_subagents", lambda *_: None)

    first_done = threading.Event()
    second_started = threading.Event()
    release = threading.Event()
    wire_snapshot = asyncio.Event()

    def execute_tool(_name, args, *, tool_call_id, **_):
        if tool_call_id == "second":
            second_started.set()
            release.wait(5)
        return args, f"Read {tool_call_id}"

    class Workers(ThreadPoolExecutor):
        def submit(self, fn, call):
            future = super().submit(fn, call)
            if call["id"] == "first":
                future.add_done_callback(lambda _: first_done.set())
            return future

    class Connected:
        async def is_disconnected(self):
            return False

    class InProcessBackend:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, url, **kwargs):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=backend.app)) as client:
                return await client.post(url, **kwargs)

        @asynccontextmanager
        async def stream(self, *_args, json, **_kwargs):
            gen = backend.stream_chat_response(
                [{"role": "system", "content": "Test."}, {"role": "user", "content": "Read both files."}],
                cid, Connected(), 1, tools_override=[{"function": {"name": "read_file"}}],
                compact_delta_history=json["compact_delta_history"], stream_stop_token=json["stream_stop_token"],
            )

            async def body():
                async for event in gen:
                    for name, data in streaming._parse_sse_blocks(event):
                        if name == "messages" and data["raw_messages"][-1].get("tool_calls"):
                            wire_snapshot.set()
                    yield event

            try:
                yield SimpleNamespace(status_code=200, aiter_text=body)
            finally:
                await gen.aclose()

    monkeypatch.setattr(tool_executor, "execute_tool_call", execute_tool)
    monkeypatch.setattr(streaming, "httpx", SimpleNamespace(
        AsyncClient=lambda **_: InProcessBackend(), Timeout=httpx.Timeout,
        ConnectError=httpx.ConnectError, HTTPError=httpx.HTTPError,
    ))

    with Workers(max_workers=2) as workers:
        monkeypatch.setattr(tool_executor, "_get_executor", lambda: workers)
        stream.task = asyncio.create_task(streaming._proxy_backend_stream(stream, {"conversation_id": cid}))
        try:
            await asyncio.wait_for(wire_snapshot.wait(), 3)
            assert await asyncio.to_thread(first_done.wait, 3)
            assert await asyncio.to_thread(second_started.wait, 3)
            url = f"{streaming.BACKEND_URL}/internal/chat/{cid}/stop"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=backend.app)) as client:
                response = await client.post(url, json={"stop_token": stream.backend_stop_token})
                assert response.status_code == 401
                response = await client.post(url, json={"stop_token": "previous-turn"}, headers=backend_auth_headers())
                assert response.status_code == 404

            snapshot = await routes.cancel_conversation_stream(cid)
            assert store.get_conversation(cid)["status"] == "interrupted"
            assert cid not in streaming.active_streams
            assert snapshot["raw_messages"] == store.get_messages(cid)
            assert [m["tool_call_id"] for m in snapshot["raw_messages"] if m["role"] == "tool"] == ["first"]
            assert snapshot["frontend_messages"][-1]["activities"][-1] == {
                "type": "tool_result", "tool_call_id": "first", "content": "Read first",
            }
            assert not release.is_set()
        finally:
            release.set()
            if not stream.task.done():
                await streaming._cancel_active_stream(cid)
