"""Regression: tool-activity cards must not dump protocol markup or Python exceptions.

Explorer (fresh page load): send ``e2e:tool``. The README.md ``read_file``
card was ``tool-activity-item complete`` (green check) and showed
``cannot unpack non-iterable NoneType``, ``CODE_INTERPRETER`` wrappers,
``[File not found: README.md]``, and the LLM-facing edit_file note.

Expected: readable result or a clear file-not-found / error; no protocol
delimiters; no Python internals; a failed tool is not marked complete.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.code_tools.tool_result_display import sanitize_tool_content_for_ui
from src.web_api.app import convert_messages_for_frontend

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "frontend" / "src" / "utils" / "toolActivityDisplay.js"

EXPLORER_DUMP = (
    "Error executing tool 'read_file': cannot unpack non-iterable NoneType object\n\n"
    "<====CODE_INTERPRETER_START====>\n"
    "--- README.md ---\n[File not found: README.md]\n\n"
    "Note: This display shows the LATEST state of each file "
    "with accurate line numbers. Always use these line numbers "
    "for edit_file calls — never use memorised line numbers.\n"
    "<====CODE_INTERPRETER_END====>"
)

MISSING_FILE = (
    "Error: File 'README.md' does not exist\n\n"
    "<====CODE_INTERPRETER_START====>\n"
    "--- README.md ---\n[File not found: README.md]\n"
    "Note: never use memorised line numbers.\n"
    "<====CODE_INTERPRETER_END====>"
)


def _frontend_result(content: str) -> dict:
    fe = convert_messages_for_frontend([
        {
            "role": "assistant",
            "content": "I'll read the file.",
            "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"file": "README.md"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "c1", "content": content},
    ])
    results = [a for a in fe[0]["activities"] if a["type"] == "tool_result"]
    assert results, "expected a tool_result activity"
    return results[0]


def _assert_clean_failure(text: str) -> None:
    assert "CODE_INTERPRETER" not in text
    assert "cannot unpack" not in text
    assert "NoneType" not in text
    assert "memorised" not in text
    assert "README.md" in text
    assert "not found" in text.lower() or text.startswith("Error")


def _eval_js(expr: str):
    script = (
        "import { sanitizeToolResultContent, toolActivityFinishClass } "
        f"from {json.dumps(HELPER.resolve().as_uri())}\n"
        f"console.log(JSON.stringify({expr}))\n"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, cwd=str(ROOT), check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout or "node helper failed")
    return json.loads(proc.stdout)


@pytest.mark.unit
def test_explorer_dump_becomes_clear_file_not_found():
    out = sanitize_tool_content_for_ui(EXPLORER_DUMP)
    _assert_clean_failure(out)
    result = _frontend_result(EXPLORER_DUMP)
    _assert_clean_failure(result["content"])
    assert result["isError"] is True


@pytest.mark.unit
def test_missing_file_keeps_error_drops_interpreter_block():
    result = _frontend_result(MISSING_FILE)
    assert result["content"] == "Error: File 'README.md' does not exist"
    assert result["isError"] is True


@pytest.mark.unit
def test_successful_read_is_not_an_error():
    ok = "The file 'README.md' (12 lines, 400 bytes) is opened in the code interpreter."
    result = _frontend_result(
        ok + "\n\n<====CODE_INTERPRETER_START====>\n1\thi\n"
        "Note: never use memorised line numbers.\n<====CODE_INTERPRETER_END====>"
    )
    assert result["content"] == ok
    assert result["isError"] is False


@pytest.mark.unit
def test_live_read_file_missing_readme_frontend_payload(tmp_workspace, monkeypatch):
    """Agent loop: read_file + panel refresh + frontend convert."""
    import src.code_sandbox as cs
    import src.code_tools.file_operations as fo
    import src.code_tools.code_interpreter_panel as cip
    from src.tool_executor import execute_tool_calls
    from src.code_tools.panel_manager import get_all

    monkeypatch.setattr(cs, "WORKSPACE", tmp_workspace)
    monkeypatch.setattr(fo, "WORKSPACE", tmp_workspace)
    monkeypatch.setattr(cip, "WORKSPACE", tmp_workspace)

    tc = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "read_file", "arguments": json.dumps({"file": "README.md"})},
    }
    messages = [{"role": "assistant", "content": "I'll read README.md.", "tool_calls": [tc]}]
    triggered = execute_tool_calls([tc], messages)
    for panel in get_all():
        idx = triggered.get(panel.name)
        if idx is not None:
            panel.refresh(messages, at_index=idx)

    raw = [m for m in messages if m.get("role") == "tool"][0]["content"]
    assert "CODE_INTERPRETER_START" in raw  # still present for the LLM

    result = [a for a in convert_messages_for_frontend(messages)[0]["activities"]
              if a["type"] == "tool_result"][0]
    _assert_clean_failure(result["content"])
    assert result["isError"] is True


@pytest.mark.unit
def test_js_card_is_failed_not_complete_for_explorer_dump():
    assert _eval_js(f"toolActivityFinishClass({{ content: {json.dumps(EXPLORER_DUMP)} }})") == "failed"
    visible = _eval_js(f"sanitizeToolResultContent({json.dumps(EXPLORER_DUMP)})")
    _assert_clean_failure(visible)
    ok = "The file 'README.md' (12 lines, 400 bytes) is opened in the code interpreter."
    assert _eval_js(f"toolActivityFinishClass({{ content: {json.dumps(ok)} }})") == "complete"


@pytest.mark.unit
def test_tool_activity_uses_failed_class_on_error():
    src = (ROOT / "frontend/src/components/ToolActivity.jsx").read_text(encoding="utf-8")
    assert "toolActivityFinishClass" in src
    assert "sanitizeToolResultContent" in src
    css = (ROOT / "frontend/src/styles/tool-activity.css").read_text(encoding="utf-8")
    assert ".tool-activity-item.failed" in css
    assert ".status-failed" in css
