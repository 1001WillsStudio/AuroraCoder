from copy import deepcopy
from types import SimpleNamespace

import pytest

from src import main_flow


@pytest.fixture
def provider(monkeypatch):
    requests = []

    def install(*outputs):
        def stream(**kwargs):
            requests.append(deepcopy(kwargs["messages"]))
            content = outputs[len(requests) - 1]
            if content is None:
                return iter([])
            return iter([SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(
                    content=content, tool_calls=None, reasoning_content=None,
                ))],
                usage=None,
            )])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=stream)))
        monkeypatch.setattr(main_flow, "provider_manager", SimpleNamespace(
            get_client=lambda _: client,
            get_config=lambda _: {"model": "fixture"},
            get_model_limits=lambda *_: (20_000, 512),
        ))
        monkeypatch.setattr(main_flow, "prefetch_primary_tools", lambda: None)
        monkeypatch.setattr(main_flow, "get_tool_definitions", lambda: [])
        monkeypatch.setattr(main_flow, "load_save_training_flag", lambda: False)
        return requests

    return install


def test_two_empty_responses_stop_without_using_the_rest_of_the_model_budget(provider):
    requests = provider(None, "  ", "This unnecessary third request should never be sent.")
    history = [{"role": "system", "content": "fixture"}, {"role": "user", "content": "Check the result."}]
    with pytest.raises(RuntimeError, match="no answer or tool call"):
        list(main_flow.generate_chat_responses_stream_native(history, max_iterations=30))
    assert len(requests) == 2
    assert len(history) == 2


def test_a_single_empty_response_can_recover_with_no_transcript_pollution(provider):
    requests = provider(None, "The result is verified.")
    history = [{"role": "system", "content": "fixture"}, {"role": "user", "content": "Check the result."}]
    events = list(main_flow.generate_chat_responses_stream_native(history, max_iterations=5))
    assert len(requests[1]) == len(requests[0]) + 1
    assert events[-1]["status"] == "completed"
    assert [msg["role"] for msg in history] == ["system", "user", "assistant"]
    assert history[-1]["content"] == "The result is verified."
