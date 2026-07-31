"""
Sanity checks for the memory master switch (settings.other.memory.enabled).

Goal being verified: with the switch off (the default), the agent behaves
exactly as it would with no memory module at all — no remember/recall/
log_gap tool schemas exposed, no stance injected, no gateway writes, no
passive extraction. Flipping it on restores the Layer 1/2a/2b behavior
already covered by test_memory_layer{1,2,3}.py.

Run with (host, conda env with gateway deps):
    python tests/test_memory_toggle.py
"""
import os
import sys
import json
import pathlib
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA_DIR = tempfile.mkdtemp()
os.environ.setdefault("AURORACODER_DATA_DIR", _DATA_DIR)
os.environ.setdefault("AURORACODER_DOCKER", "0")

from memory import settings as mem_settings
from src.core_tools import memory_client
from src.tool_definitions import get_tool_definitions, memory_filter_tools, MEMORY_TOOL_NAMES
from memory.ops import extractor


def _write_settings(other_memory: dict) -> None:
    """Overwrite settings.json in the shared temp data dir. Every reader
    here (gateway.settings_store, src.core_tools.memory_client) re-reads
    the file on every call, so this is enough to flip state mid-test
    without needing to re-import anything."""
    path = pathlib.Path(os.environ["AURORACODER_DATA_DIR"]) / "settings.json"
    path.write_text(json.dumps({"other": {"memory": other_memory}}), encoding="utf-8")


def test_memory_disabled_by_default():
    _write_settings({})  # no "enabled" key at all — simulates a fresh install
    assert mem_settings.memory_enabled() is False
    assert mem_settings.passive_extraction_enabled() is False
    assert mem_settings.heavy_ops_enabled() is False
    assert memory_client.memory_enabled() is False


def test_sub_flags_require_the_master_switch():
    # Master off, sub-flags explicitly on — must still be off.
    _write_settings({"enabled": False, "passive_enabled": True, "heavy_ops_enabled": True})
    assert mem_settings.passive_extraction_enabled() is False
    assert mem_settings.heavy_ops_enabled() is False

    # Master on, sub-flags default — passive on (default True), heavy off (default False).
    _write_settings({"enabled": True})
    assert mem_settings.passive_extraction_enabled() is True
    assert mem_settings.heavy_ops_enabled() is False

    # Master on, both sub-flags explicitly on.
    _write_settings({"enabled": True, "heavy_ops_enabled": True})
    assert mem_settings.heavy_ops_enabled() is True


def test_gateway_and_backend_agree_on_the_flag():
    """Two independent readers (gateway process, backend process) must
    reach the same conclusion from the same settings.json — they gate the
    same feature from two separate processes."""
    for value in (False, True):
        _write_settings({"enabled": value})
        assert mem_settings.memory_enabled() is value
        assert memory_client.memory_enabled() is value


def test_tool_definitions_exclude_memory_tools_when_disabled():
    _write_settings({"enabled": False})
    names = {t["function"]["name"] for t in get_tool_definitions()}
    assert not (names & MEMORY_TOOL_NAMES), names & MEMORY_TOOL_NAMES

    _write_settings({"enabled": True})
    names = {t["function"]["name"] for t in get_tool_definitions()}
    assert MEMORY_TOOL_NAMES.issubset(names), MEMORY_TOOL_NAMES - names


def test_memory_filter_tools_helper_directly():
    fake_tools = [
        {"function": {"name": "read_file"}},
        {"function": {"name": "remember"}},
        {"function": {"name": "recall"}},
        {"function": {"name": "log_gap"}},
    ]
    _write_settings({"enabled": False})
    filtered = memory_filter_tools(fake_tools)
    assert {t["function"]["name"] for t in filtered} == {"read_file"}

    _write_settings({"enabled": True})
    filtered = memory_filter_tools(fake_tools)
    assert {t["function"]["name"] for t in filtered} == {"read_file", "remember", "recall", "log_gap"}


def test_gateway_routes_are_inert_when_disabled():
    from fastapi.testclient import TestClient
    from gateway.api import app

    _write_settings({"enabled": False})
    client = TestClient(app)

    r = client.get("/api/memory/stance")
    assert r.status_code == 200 and r.json()["stance"] == ""

    r = client.post("/api/memory/remember", json={
        "content": "x", "description": "y", "plane": "world", "type": "reference", "scope": "project",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "disabled" in body["reason"]

    r = client.get("/api/memory/recall", params={"query": "anything"})
    assert r.status_code == 200 and r.json()["results"] == []

    r = client.post("/api/memory/gaps", json={"question": "does this get logged?"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "disabled" in body["reason"]


def test_extraction_no_ops_when_disabled_even_with_a_nomination():
    """Passive extraction must stay off when the master switch is off,
    regardless of passive_enabled, and regardless of whether the
    transcript contains a `remember` nomination."""
    _write_settings({"enabled": False, "passive_enabled": True})
    msgs = [
        {"role": "user", "content": "Always run ruff before you say you're done."},
        {
            "role": "assistant", "content": "Got it.",
            "tool_calls": [{
                "id": "call_1",
                "function": {"name": "remember", "arguments": json.dumps({
                    "content": "Run ruff before finishing.",
                    "description": "User preference: run ruff before finishing",
                    "plane": "stance", "type": "preference", "scope": "user", "confidence": "high",
                })},
            }],
        },
        {"role": "tool", "content": "Noted", "tool_call_id": "call_1"},
    ]

    calls = []
    extractor.get_memory_extraction_config = lambda: {
        "provider_id": "fake", "base_url": "http://fake", "api_key": "fake-key", "model": "fake-model",
    }
    extractor.OpenAI = lambda base_url, api_key: calls.append(1)
    result = extractor.run_extraction("conv-disabled", msgs)
    assert result == [] and not calls, "must not even construct an LLM client while disabled"


def test_system_prompt_omits_all_memory_mention_when_disabled():
    """Exercises the exact template substitution main_flow.py performs,
    without importing main_flow itself (which pulls in the shell sandbox).
    A disabled agent's system prompt must not mention memory tools at
    all — not even the static guideline bullet."""
    from src.config import SYSTEM_MESSAGE_TEMPLATE

    disabled_prompt = SYSTEM_MESSAGE_TEMPLATE.format(
        current_time="now", vnc_instructions="", terminal_env_note="",
        toolstore_tools="", workspace_tree="", memory_section="",
    )
    assert "remember" not in disabled_prompt.lower()
    assert "recall" not in disabled_prompt.lower()

    enabled_prompt = SYSTEM_MESSAGE_TEMPLATE.format(
        current_time="now", vnc_instructions="", terminal_env_note="",
        toolstore_tools="", workspace_tree="",
        memory_section="- **Memory**: you have `remember`/`recall` tools...\n[STANCE]\n",
    )
    assert "remember" in enabled_prompt.lower()
    assert "[STANCE]" in enabled_prompt


def _run_all():
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\nALL {len(tests)} MEMORY TOGGLE CHECKS PASSED")


if __name__ == "__main__":
    _run_all()
