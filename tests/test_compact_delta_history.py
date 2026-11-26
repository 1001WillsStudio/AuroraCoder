from contextlib import asynccontextmanager
from importlib import import_module
from types import SimpleNamespace

import pytest

from gateway import streaming
from gateway.conversation_store import ConversationStore

backend = import_module("src.web_api.app")


class Connected:
    async def is_disconnected(self):
        return False


@pytest.mark.parametrize("compact", [False, True])
async def test_backend_compacts_only_opted_in_deltas(monkeypatch, compact):
    user = {"role": "user", "content": "Start"}
    preview = {"role": "assistant", "content": ""}
    answer = {"role": "assistant", "content": "Finished"}

    def generate(**kwargs):
        yield {"messages": [user, preview], "status": "running"}
        yield {"messages": [user, answer], "status": "running", "llm_delta": {"content": "Finished"}}
        yield {"messages": [user, answer], "status": "completed"}

    monkeypatch.setattr(backend, "generate_chat_responses_stream_native", generate)
    monkeypatch.setattr(backend, "cancel_active_subagents", lambda *_: None)
    events = [
        parsed
        async for event in backend.stream_chat_response(
            [user], f"compact-{compact}", Connected(), 2, compact_delta_history=compact,
        )
        for parsed in streaming._parse_sse_blocks(event)
    ]
    delta, = [event for name, event in events if name == "delta"]
    if compact:
        assert delta["raw_delta"] == {"snapshot_seq": 1, "message": answer}
        assert "raw_messages" not in delta
        assert delta["messages"][-1]["content"] == "Finished"
    else:
        assert delta["raw_messages"] == [user, answer]
        assert "raw_delta" not in delta
    done, = [event for name, event in events if name == "done"]
    assert done["raw_messages"] == [user, answer]


async def test_gateway_preserves_reconstructed_history_on_stop(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    stream = streaming.ActiveStream(conversation_id=store.create_conversation())
    monkeypatch.setattr(streaming, "store", store)
    monkeypatch.setattr(streaming, "_schedule_memory_distillation", lambda *_: None)
    user = {"role": "user", "content": "Start"}
    preview = {"role": "assistant", "content": ""}
    partial = {"role": "assistant", "content": "Partial answer", "reasoning_content": "Reasoning"}

    class Backend:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        @asynccontextmanager
        async def stream(self, *_args, **kwargs):
            assert kwargs["json"]["compact_delta_history"] is True

            async def body():
                yield streaming._format_sse("messages", {
                    "seq": 1, "status": "running", "raw_messages": [user, preview],
                })
                yield streaming._format_sse("delta", {
                    "seq": 2, "status": "running", "raw_delta": {"snapshot_seq": 1, "message": partial},
                    "delta": {"content": "Partial answer"}, "messages": [user, partial],
                })
                # A compact frame from another snapshot cannot replace stored output.
                yield streaming._format_sse("delta", {
                    "seq": 3, "status": "running",
                    "raw_delta": {"snapshot_seq": 0, "message": preview},
                })
                stream.cancel_event.set()

            yield SimpleNamespace(status_code=200, aiter_text=body)

    monkeypatch.setattr(streaming, "httpx", SimpleNamespace(
        AsyncClient=lambda **_: Backend(), Timeout=lambda *_: None, ConnectError=ConnectionError,
    ))
    await streaming._proxy_backend_stream(stream, {"conversation_id": stream.conversation_id})

    stored = store.get_messages(stream.conversation_id)
    assert [{key: value for key, value in message.items() if key != "ts"} for message in stored] == [user, partial]
    assert store.get_conversation(stream.conversation_id)["status"] == "interrupted"
