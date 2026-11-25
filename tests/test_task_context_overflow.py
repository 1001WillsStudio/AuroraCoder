from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError

from src import main_flow
from src.task_checklist import AgentTaskTools


@pytest.fixture
def reject_provider(monkeypatch, tmp_path):
    def install(body):
        response = httpx.Response(400, request=httpx.Request("POST", "http://provider.invalid/v1/chat"))
        error = BadRequestError("Request rejected", response=response, body=body)

        def reject(**_):
            raise error

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=reject)))
        monkeypatch.setattr(main_flow, "provider_manager", SimpleNamespace(
            get_client=lambda _: client,
            get_config=lambda _: {"model": "fixture"},
            get_model_limits=lambda *_: (20_000, 2048),
        ))
        monkeypatch.setattr(main_flow, "prefetch_primary_tools", lambda: None)
        monkeypatch.setattr(main_flow, "get_tool_definitions", lambda: [])
        monkeypatch.setattr(main_flow, "load_save_training_flag", lambda: False)
        monkeypatch.setattr(main_flow, "__file__", str(tmp_path / "src" / "main_flow.py"))
        return error

    return install


@pytest.mark.parametrize("body", [
    {"code": "context_length_exceeded", "message": "Input too long."},
    {"error": {"type": "BadRequestError", "code": 400, "param": "input_tokens",
               "message": "This model's maximum context length is 100 tokens. Your request has 200 input tokens."}},
])
def test_active_task_reaches_context_handoff_on_explicit_provider_rejection(reject_provider, body):
    reject_provider(body)
    task = AgentTaskTools("fixture", "fixture-token", {"status": "active", "objective": "Verify the project"})
    messages = [{"role": "system", "content": "Fixture"}, {"role": "user", "content": "Continue testing"}]
    events = list(main_flow.generate_chat_responses_stream_native(messages, task_tools=task))
    assert [event["status"] for event in events] == ["context_limit_reached"]
    assert events[0]["messages"] == messages


@pytest.mark.parametrize(("body", "active"), [
    ({"code": "context_length_exceeded"}, False),
    ({"code": "invalid_value", "message": "Tool payload rejected."}, True),
    ({"code": 400, "param": "max_tokens",
      "message": "'max_tokens' cannot be greater than the model's maximum context length."}, True),
])
def test_unrelated_bad_requests_and_plain_chat_keep_the_provider_error(reject_provider, body, active):
    expected = reject_provider(body)
    task = AgentTaskTools("fixture", "fixture-token", {"status": "active"}) if active else None
    messages = [{"role": "system", "content": "Fixture"}, {"role": "user", "content": "Continue testing"}]
    with pytest.raises(BadRequestError) as raised:
        list(main_flow.generate_chat_responses_stream_native(messages, task_tools=task))
    assert raised.value is expected
