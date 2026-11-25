import json
from types import SimpleNamespace

import pytest

from src import main_flow, provider_diagnostics


class ProviderFailure(Exception):
    status_code = 400
    body = {"error": {"message": "UPSTREAM_PRIVATE_TEST", "param": "messages[8].thinking"}}
    response = SimpleNamespace(text="RAW_PROVIDER_BODY_TEST")


def test_provider_diagnostics_keep_schema_evidence_and_exclude_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_diagnostics, "DATA_DIR", tmp_path)
    provider_diagnostics.record_provider_failure(
        ProviderFailure("EXCEPTION_PRIVATE_TEST"), "test-provider", "test-model", "test-conversation",
        {"messages": [
            {"role": "user", "content": "USER_PRIVATE_TEST"},
            {"role": "assistant", "thinking": "THINKING_PRIVATE_TEST", "tool_calls": [
                {"function": {"name": "read_file", "arguments": "TOOLS_PRIVATE_TEST"}},
            ]},
        ], "tools": [{"name": "read_file"}]},
    )

    line = (tmp_path / "diagnostics" / "llm_failures.jsonl").read_text(encoding="utf-8")
    entry = json.loads(line)
    assert entry["status"] == 400
    assert entry["error"] == "ProviderFailure"
    assert entry["param"] == "messages[8].thinking"
    assert entry["request"] == {
        "roles": {"user": 1, "assistant": 1},
        "fields": {"content": 1, "thinking": 1, "tool_calls": 1}, "tool_count": 1,
    }
    assert "PRIVATE_TEST" not in line and "RAW_PROVIDER_BODY_TEST" not in line


def test_unwritable_diagnostics_cannot_mask_original_provider_error(tmp_path, monkeypatch):
    failure = ProviderFailure("Provider rejected the request")
    (tmp_path / "diagnostics").write_text("This is a file")
    monkeypatch.setattr(provider_diagnostics, "DATA_DIR", tmp_path)

    def create(**_kwargs):
        raise failure

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(main_flow.provider_manager, "get_client", lambda _provider: client)
    monkeypatch.setattr(main_flow.provider_manager, "get_config", lambda _provider: {})
    monkeypatch.setattr(main_flow.provider_manager, "get_model_limits", lambda *_: (4096, 1024))
    monkeypatch.setattr(main_flow, "prefetch_primary_tools", lambda: None)
    monkeypatch.setattr(main_flow, "load_save_training_flag", lambda: False)

    with pytest.raises(ProviderFailure) as reported:
        list(main_flow.generate_chat_responses_stream_native([
            {"role": "system", "content": "Test."},
            {"role": "user", "content": "Test."},
        ]))
    assert reported.value is failure
