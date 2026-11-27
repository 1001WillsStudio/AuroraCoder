from types import SimpleNamespace

import pytest
from httpx import ReadError

from src import main_flow


def chunk(content="", tool_calls=None, finish_reason="stop"):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)], usage=None)


def fail_after(chunk_to_yield, exc):
    yield chunk_to_yield
    raise exc


def configure(monkeypatch, responses):
    requests = []
    streams = iter(responses)

    def create(**kwargs):
        requests.append(list(kwargs["messages"]))
        return next(streams)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(main_flow.provider_manager, "get_client", lambda _provider: client)
    monkeypatch.setattr(main_flow.provider_manager, "get_config", lambda _provider: {})
    monkeypatch.setattr(main_flow.provider_manager, "get_model_limits", lambda *_: (4096, 1024))
    monkeypatch.setattr(main_flow, "prefetch_primary_tools", lambda: None)
    monkeypatch.setattr(main_flow, "load_save_training_flag", lambda: False)
    return requests


def run_turn(max_iterations):
    return main_flow.generate_chat_responses_stream_native(
        [{"role": "system", "content": "Test."}, {"role": "user", "content": "Read the file."}],
        max_iterations=max_iterations,
    )


def test_retry_keeps_iteration_and_does_not_reexecute_finished_tool(monkeypatch):
    call = SimpleNamespace(index=0, id="call1",
                           function=SimpleNamespace(name="read_file", arguments="{}"))
    requests = configure(monkeypatch, [
        iter([chunk(tool_calls=[call])]),
        fail_after(chunk("Incomplete"), ReadError("socket closed")),
        iter([chunk("Finished")]),
    ])
    executed = []

    def execute(calls, messages, **_):
        executed.extend(calls)
        messages.append({"role": "tool", "tool_call_id": "call1", "content": "Read once"})
        return {}

    monkeypatch.setattr(main_flow, "execute_tool_calls", execute)
    states = list(run_turn(max_iterations=2))

    assert [call["id"] for call in executed] == ["call1"]
    assert requests[1] == requests[2]
    assert requests[2][-1]["content"] == "Read once"
    assert states[-1]["status"] == "completed"
    assert states[-1]["messages"][-1]["content"] == "Finished"
    assert all(msg["content"] != "Incomplete" for msg in states[-1]["messages"])


def test_transport_retry_is_bounded_and_closes_each_stream(monkeypatch):
    closed = []

    def interrupted(index):
        try:
            yield from fail_after(chunk("Incomplete"), ReadError("socket closed"))
        finally:
            closed.append(index)

    count = main_flow.MAX_STREAMING_RETRIES + 1
    requests = configure(monkeypatch, [interrupted(index) for index in range(count)])

    final = list(run_turn(max_iterations=1))[-1]
    assert final["status"] == "error"
    assert "Connection lost while reading the model response" in final["error"]
    assert [msg["role"] for msg in final["messages"]] == ["system", "user"]
    assert len(requests) == count
    assert closed == list(range(count))


def test_stream_protocol_error_is_reported_without_a_retry(monkeypatch):
    requests = configure(monkeypatch, [fail_after(chunk(), ValueError("invalid payload"))])
    with pytest.raises(ValueError, match="invalid payload"):
        list(run_turn(max_iterations=1))
    assert len(requests) == 1


@pytest.mark.parametrize(
    "arguments, ids, finish_reason",
    [
        ('{"file":', ("first",), "tool_calls"),
        (" ", ("first",), "tool_calls"),
        ("[]", ("first",), "tool_calls"),
        ("{}", ("first", "first"), "tool_calls"),
        ("{}", ("",), "tool_calls"),
        ("{}", ("first",), "length"),
        ("{}", ("first",), None),
    ],
)
def test_unfinished_tool_calls_are_retried_before_commit(monkeypatch, arguments, ids, finish_reason):
    calls = [
        SimpleNamespace(index=index, id=call_id, function=SimpleNamespace(name="read_file", arguments=arguments))
        for index, call_id in enumerate(ids)
    ]
    requests = configure(monkeypatch, [
        iter([chunk(tool_calls=calls, finish_reason=finish_reason)]),
        iter([chunk("Recovered")]),
    ])
    executed = []
    monkeypatch.setattr(main_flow, "execute_tool_calls", lambda *args, **kwargs: executed.append(args))

    final = list(run_turn(1))[-1]

    assert requests[0] == requests[1]
    assert not executed
    assert final["status"] == "completed"
    assert final["messages"][-1]["content"] == "Recovered"
    assert all("tool_calls" not in message for message in final["messages"])


def test_truncated_text_survives_without_marking_the_turn_completed(monkeypatch):
    configure(monkeypatch, [iter([chunk("Partial answer", finish_reason="length")])])
    final = list(run_turn(1))[-1]
    assert final["status"] == "interrupted"
    assert final["messages"][-1]["content"] == "Partial answer"


def test_sdk_close_error_preserves_stream_retry(monkeypatch):
    class Interrupted:
        def __iter__(self):
            raise ReadError("stream interrupted")

        def close(self):
            raise OSError("close failed")

    requests = configure(monkeypatch, [Interrupted(), iter([chunk("Recovered")])])
    final = list(run_turn(1))[-1]
    assert len(requests) == 2
    assert final["status"] == "completed"
