import json
import sys

import pytest

from gateway.conversation_store import ConversationStore
from src.providers import messages_for_provider
from src.tool_evidence import (
    CONTEXT_CUT,
    TERMINAL_EVIDENCE,
    compact_provider_view,
    read_result,
)
from src.tool_executor import execute_tool_calls


def _call(call_id, name, arguments="{}"):
    return {
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _batch(call_id, body):
    return [
        {"role": "assistant", "tool_calls": [_call(call_id, "read_file")]},
        {"role": "tool", "tool_call_id": call_id, "content": body},
    ]


def test_compaction_reuses_saved_cut_and_keeps_original():
    content = ("line text with several words\n" * 1500)
    diagnostics = "FAILED tests/test_checkout.py::test_expected"
    original = content[:18000] + "\n" + diagnostics + "\n" + content[18000:]
    messages = [{"role": "user", "content": "Keep the original request."}]
    messages.extend(_batch("old", original))
    for index in range(24):
        messages.extend(_batch(str(index), "recent evidence"))

    outgoing = messages_for_provider(messages)
    compact_provider_view(messages, outgoing, "balanced", 10000, 1000, known_window=False)
    saved_cut = messages[2][CONTEXT_CUT]
    assert "Omitted" in outgoing[2]["content"]
    assert diagnostics in outgoing[2]["content"]
    assert messages[2]["content"] == original
    assert outgoing[-1]["content"] == "recent evidence"

    outgoing_again = messages_for_provider(messages)
    compact_provider_view(messages, outgoing_again, "compact", 10000, 1000, known_window=False)
    assert messages[2][CONTEXT_CUT] == saved_cut
    assert outgoing_again[2]["content"] == outgoing[2]["content"]
    assert CONTEXT_CUT not in outgoing_again[2]
    assert "line text" in read_result(messages, {"tool_call_id": "old", "offset": saved_cut[0]})


@pytest.mark.skipif(sys.platform == "win32", reason="Requires AuroraCoder's Linux shell")
def test_fork_keeps_omitted_terminal_evidence_after_source_deletion(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_MAX_OUTPUT_CHARS", "120")
    store = ConversationStore(tmp_path)
    source = store.create_conversation()
    unrelated = store.create_conversation()
    calls = [_call(
        "terminal-evidence", "run_terminal_command",
        json.dumps({"command": """python3 -c 'print("a" * 250 + "unique omitted proof" + "b" * 250)'"""}),
    )]
    transcript = [
        {"role": "user", "content": "Run one check"},
        {"role": "assistant", "tool_calls": calls},
    ]
    execute_tool_calls(calls, transcript, conversation_id=source)
    assert "unique omitted proof" not in transcript[-1]["content"]
    assert "unique omitted proof" in transcript[-1][TERMINAL_EVIDENCE]
    assert TERMINAL_EVIDENCE not in messages_for_provider(transcript)[-1]
    store.save_messages(source, transcript)
    store.update_status(source, "completed")

    fork = store.fork_conversation(source)
    store.delete_conversation(source)
    reopened = ConversationStore(tmp_path)
    fork_messages = reopened.get_messages(fork["id"])
    read_calls = [_call(
        "recover", "read_original_tool_result", '{"tool_call_id": "terminal-evidence"}',
    )]
    execute_tool_calls(read_calls, fork_messages, conversation_id=fork["id"])
    assert "unique omitted proof" in fork_messages[-1]["content"]
    assert "no saved omitted output" in read_result(
        reopened.get_messages(unrelated), {"tool_call_id": "terminal-evidence"},
    )
