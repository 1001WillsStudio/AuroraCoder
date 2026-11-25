"""Validate that a continuation is tied to a successful new-tool call and the original setup."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx

from gateway import routes, streaming
from gateway.api import app
from gateway.conversation_store import ConversationStore
from src.config import CONTINUATION_TOOL_RESULT


def handoff(tool_id, prompt):
    return {"role": "assistant", "tool_calls": [{
        "id": tool_id, "type": "function",
        "function": {"name": "continue_as_new_chat", "arguments": json.dumps({"prompt": prompt})},
    }]}


def response(tool_id, content=CONTINUATION_TOOL_RESULT):
    return {"role": "tool", "tool_call_id": tool_id, "content": content}


def test_handoff_ignores_old_unfinished_and_invalid_calls():
    old = [handoff("old", "Old work"), response("old")]
    assert streaming._scan_for_continuation(old, {"old"}) is None
    assert streaming._scan_for_continuation([*old, handoff("new", "Fresh")], {"old"}) is None
    invalid = [handoff("new", []), response("new")]
    assert streaming._scan_for_continuation([*old, *invalid], {"old"}) is None
    denied = [handoff("new", "Fresh"), response("new", "Error: tool unavailable")]
    assert streaming._scan_for_continuation([*old, *denied], {"old"}) is None
    assert streaming._scan_for_continuation(
        [*old, handoff("new", "Fresh"), response("new"), *invalid], {"old"},
    ) == "Fresh"


def test_user_scope_survives_more_than_one_handoff():
    opening = {"role": "user", "content": "Fix the launcher. Preserve the cached base image."}
    follow_up = {"role": "user", "content": "Inspect the GPU launcher too. Only edit scripts."}
    first = streaming._continuation_message([opening, follow_up], "The first agent discussed a base rebuild.")
    second = streaming._continuation_message([
        {"role": "user", "content": first}, {"role": "user", "content": "Keep both existing default ports."},
    ], "Check the remaining launcher.")
    assert opening["content"] in second
    assert follow_up["content"] in second
    assert "Keep both existing default ports." in second
    assert "The first agent discussed a base rebuild." not in second


def test_stored_handoff_skips_stale_successors_and_keeps_the_source(tmp_path):
    store = ConversationStore(tmp_path)
    first = store.create_conversation()
    second = store.create_conversation()
    last = store.create_conversation()
    store.mark_continued(first, second)
    store.mark_continued(second, last)
    reloaded = ConversationStore(tmp_path)
    assert reloaded.latest_continuation(first) == last
    assert reloaded.latest_continuation(second) == last
    reloaded.delete_conversation(last)
    assert reloaded.latest_continuation(first) == second
    assert reloaded.latest_continuation(second) is None
    assert reloaded.get_conversation(first)["status"] == "continued"


def test_gateway_starts_child_after_tool_result_and_retains_model(tmp_path, monkeypatch):
    async def run():
        store = ConversationStore(tmp_path)
        monkeypatch.setattr(streaming, "store", store)
        monkeypatch.setattr(streaming, "active_streams", {})
        monkeypatch.setattr(streaming, "_memory_ops_executor", SimpleNamespace(submit=lambda *_: None))
        monkeypatch.setattr(streaming, "_schedule_memory_distillation", lambda *_: None)
        cid = store.create_conversation()
        old = [handoff("old", "Old work"), response("old")]
        user = {"role": "user", "content": "Verify the current branch without changing it."}
        pending = [*old, user, handoff("new", "Continue checking the logs.")]
        sent = []
        child_started = asyncio.Event()

        class Backend:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            @asynccontextmanager
            async def stream(self, _method, _url, json, **_kwargs):
                sent.append(json)

                async def events():
                    if json["conversation_id"] == cid:
                        yield streaming._format_sse("messages", {
                            "raw_messages": pending, "status": "running", "provider": "configured",
                        })
                        assert len(sent) == 1
                        finished = {"raw_messages": [*pending, response("new")], "status": "completed",
                                    "provider": "configured"}
                        yield streaming._format_sse("messages", finished)
                        yield streaming._format_sse("done", finished)
                    else:
                        child_started.set()
                        yield streaming._format_sse("done", {"status": "completed"})

                yield SimpleNamespace(status_code=200, aiter_text=events)

        monkeypatch.setattr(streaming, "httpx", SimpleNamespace(
            AsyncClient=lambda **_: Backend(), Timeout=httpx.Timeout, ConnectError=httpx.ConnectError,
        ))
        stream = streaming.ActiveStream(conversation_id=cid)
        await streaming._proxy_backend_stream(stream, {
            "conversation_id": cid, "messages": old, "message": user["content"],
            "provider_id": "configured", "model": "selected-model", "max_iterations": 75,
        })
        await asyncio.wait_for(child_started.wait(), timeout=3)

        first, child = sent
        assert child["conversation_id"] == stream.new_conversation_id
        assert child["provider_id"] == first["provider_id"]
        assert child["model"] == first["model"]
        assert child["max_iterations"] == 75
        assert user["content"] in child["message"]
        assert "Continue checking the logs." in child["message"]
        assert store.get_conversation(cid)["status"] == "continued"
        reloaded = ConversationStore(tmp_path)
        monkeypatch.setattr(routes, "store", reloaded)
        monkeypatch.setattr(routes, "active_streams", {})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
            replay = await client.get(f"/api/conversations/{cid}/stream")
            assert replay.status_code == 200
            assert f'"new_conversation_id": "{stream.new_conversation_id}"' in replay.text
            source = await client.get(f"/api/conversations/{cid}")
            assert source.json()["new_conversation_id"] == stream.new_conversation_id
            assert source.json()["messages"][-1]["tool_call_id"] == "new"

    asyncio.run(run())
