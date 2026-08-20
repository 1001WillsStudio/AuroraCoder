"""Regression: the tool-activity card must not dump interpreter protocol.

Explorer / reviewer (fresh desktop load, ``e2e:tool``): the README.md
``read_file`` card was ``tool-activity-item complete`` (green check) and
showed ``Error: File 'README.md' does not exist`` plus
``CODE_INTERPRETER`` wrappers, ``[File not found: README.md]``, and the
LLM-facing edit_file line-number note.

Expected: strip that panel dump in the card and mark an ``Error:``
result as failed, not complete.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "frontend" / "src" / "utils" / "toolActivityDisplay.js"

# What the card actually receives today: tool error + interpreter block.
MISSING_FILE = (
    "Error: File 'README.md' does not exist\n\n"
    "<====CODE_INTERPRETER_START====>\n"
    "--- README.md ---\n[File not found: README.md]\n"
    "Note: This display shows the LATEST state of each file "
    "with accurate line numbers. Always use these line numbers "
    "for edit_file calls — never use memorised line numbers.\n"
    "<====CODE_INTERPRETER_END====>"
)


def _eval_js(expr: str):
    script = (
        "import { stripPanelMarkup, isFailedToolOutput, toolActivityFinishClass } "
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
def test_card_strips_interpreter_keeps_file_error():
    visible = _eval_js(f"stripPanelMarkup({json.dumps(MISSING_FILE)})")
    assert visible == "Error: File 'README.md' does not exist"
    assert "CODE_INTERPRETER" not in visible
    assert "memorised" not in visible
    assert _eval_js(f"isFailedToolOutput({json.dumps(visible)})") is True
    assert _eval_js(
        f"toolActivityFinishClass({{ content: {json.dumps(MISSING_FILE)} }})"
    ) == "failed"


@pytest.mark.unit
def test_successful_read_stays_complete():
    ok = "The file 'README.md' (12 lines, 400 bytes) is opened in the code interpreter."
    dumped = (
        ok + "\n\n<====CODE_INTERPRETER_START====>\n1\thi\n"
        "Note: never use memorised line numbers.\n<====CODE_INTERPRETER_END====>"
    )
    assert _eval_js(f"stripPanelMarkup({json.dumps(dumped)})") == ok
    assert _eval_js(f"toolActivityFinishClass({{ content: {json.dumps(dumped)} }})") == "complete"
    assert _eval_js(f"isFailedToolOutput({json.dumps(ok)})") is False


@pytest.mark.unit
def test_tool_activity_uses_failed_class_on_error():
    src = (ROOT / "frontend/src/components/ToolActivity.jsx").read_text(encoding="utf-8")
    assert "toolActivityFinishClass" in src
    assert "stripPanelMarkup" in src
    css = (ROOT / "frontend/src/styles/tool-activity.css").read_text(encoding="utf-8")
    assert ".tool-activity-item.failed" in css
    assert ".status-failed" in css
