"""Regression: the chat composer must grow with a multiline draft.

Explorer (fresh page load, 390x844): tap Ask me anything…, paste eight
lines. The textarea stayed at 60px (rows=1) even though CSS max-height
is 200px and scrollHeight was ~228px. Only the last lines showed, and
the top visible line was sliced by the input border. No auto-resize
ran in the bundle.

There is no JS test runner here, so behaviour is locked two ways:
  * the extracted helpers in ``frontend/src/utils/composerResize.js``
    run under Node (hermetic: no network, no real DOM);
  * a source scan asserts ChatInput applies them when the draft changes
    and that CSS still caps at 200px.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "frontend" / "src" / "utils" / "composerResize.js"
CHAT_INPUT = ROOT / "frontend" / "src" / "components" / "ChatInput.jsx"
INPUT_CSS = ROOT / "frontend" / "src" / "styles" / "input.css"


def _eval_js(expr: str):
    script = (
        "import {\n"
        "  COMPOSER_MAX_HEIGHT_PX,\n"
        "  composerHeight,\n"
        "  applyComposerResize,\n"
        f"}} from {json.dumps(HELPER.resolve().as_uri())}\n"
        f"const out = {expr}\n"
        "console.log(JSON.stringify(out))\n"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout or "node helper failed")
    return json.loads(proc.stdout)


@pytest.mark.unit
def test_eight_line_draft_grows_to_css_max_not_stuck_at_60px():
    """Reported case: scrollHeight 228, computed max-height 200, was 60px."""
    result = _eval_js(
        "(() => {"
        "  const el = { scrollHeight: 228, style: { height: '60px' } };"
        "  const applied = applyComposerResize(el, 200);"
        "  return { applied, height: el.style.height, overflowY: el.style.overflowY };"
        "})()"
    )
    assert result["applied"] == 200
    assert result["height"] == "200px"
    assert result["overflowY"] == "auto"


@pytest.mark.unit
def test_mid_draft_grows_with_content_instead_of_staying_at_one_row():
    """Four lines (~132px) must become 132px, not remain the 60px rows=1 box."""
    result = _eval_js(
        "(() => {"
        "  const el = { scrollHeight: 132, style: { height: '60px' } };"
        "  const applied = applyComposerResize(el, 200);"
        "  return { applied, height: el.style.height, overflowY: el.style.overflowY };"
        "})()"
    )
    assert result["applied"] == 132
    assert result["height"] == "132px"
    assert result["overflowY"] == "hidden"


@pytest.mark.unit
def test_composer_height_caps_and_passes_through():
    assert _eval_js("composerHeight(228, 200)") == 200
    assert _eval_js("composerHeight(60, 200)") == 60
    assert _eval_js("COMPOSER_MAX_HEIGHT_PX") == 200


@pytest.mark.unit
def test_chat_input_resizes_when_the_draft_changes():
    """Controlled value updates (type, paste, example click) must resize."""
    src = CHAT_INPUT.read_text(encoding="utf-8")
    assert "applyComposerResize" in src
    assert re.search(
        r"from ['\"]\.\./utils/composerResize(?:\.js)?['\"]",
        src,
    ), "ChatInput must import the composer resize helper"
    match = re.search(
        r"useLayoutEffect\(\(\)\s*=>\s*\{(.*?)\},\s*\[([^\]]*)\]\)",
        src,
        flags=re.DOTALL,
    )
    assert match, "ChatInput must resize in a useLayoutEffect so the field grows before paint"
    assert "applyComposerResize" in match.group(1)
    assert "value" in match.group(2)


@pytest.mark.unit
def test_css_still_caps_composer_at_200px():
    css = INPUT_CSS.read_text(encoding="utf-8")
    block = re.search(r"\.chat-input\s*\{([^}]+)\}", css)
    assert block, ".chat-input rule missing"
    body = block.group(1)
    assert re.search(r"max-height\s*:\s*200px", body), (
        "composer must keep CSS max-height: 200px (the reported cap)"
    )
    assert re.search(r"overflow-y\s*:\s*auto", body), (
        "capped drafts must be scrollable so a line is not trapped under the border"
    )
