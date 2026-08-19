"""Regression: long unbreakable tokens in chat messages must wrap or scroll.

Explorer (390x844, fresh load): type into Ask me anything… and send

    Check this token: SUPERCALIFRAGILISTICEXPIALIDOCIOUS_AND_THEN_SOME_MORE_
    UNBREAKABLE_TEXT_THAT_SHOULD_WRAP_OR_OVERFLOW_ON_A_NARROW_PHONE_SCREEN_
    ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789

The user-message ``<p>`` computed ``word-break: normal`` and
``overflow-wrap: normal``.  scrollWidth was ~1598px against a ~274px
client width; ``.main-content { overflow: hidden }`` clipped the rest,
so the token could not be read or copied.

There is no JS test runner here, so the CSS contract is locked by scanning
``frontend/src/styles/messages.css`` (hermetic: source text only).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MESSAGES_CSS = ROOT / "frontend" / "src" / "styles" / "messages.css"

# Values that actually break an unbreakable token (or let the reader scroll).
_WRAP = r"(?:anywhere|break-word)"
_BREAK = r"(?:break-word|break-all)"
_SCROLL = r"(?:auto|scroll)"


def _css() -> str:
    return MESSAGES_CSS.read_text(encoding="utf-8")


def _rule_body(css: str, selector: str) -> str:
    """Body of the first ``selector { ... }`` that is not a longer selector."""
    pattern = re.compile(
        rf"(?:^|[\n}}])\s*{re.escape(selector)}\s*\{{([^}}]*)\}}",
        re.MULTILINE,
    )
    match = pattern.search(css)
    if match is None:
        pytest.fail(f"no {selector} {{ }} rule in messages.css")
    return match.group(1)


def _allows_long_token(body: str) -> bool:
    if re.search(rf"overflow-wrap\s*:\s*{_WRAP}\b", body):
        return True
    if re.search(rf"word-wrap\s*:\s*{_WRAP}\b", body):
        return True
    if re.search(rf"word-break\s*:\s*{_BREAK}\b", body):
        return True
    if re.search(rf"overflow-x\s*:\s*{_SCROLL}\b", body):
        return True
    return False


@pytest.mark.unit
def test_message_text_wraps_or_scrolls_unbreakable_tokens():
    """``.message-text`` is the user paragraph and assistant markdown host."""
    body = _rule_body(_css(), ".message-text")
    assert _allows_long_token(body), (
        ".message-text must set overflow-wrap/word-break (or overflow-x: auto) "
        "so a long identifier on a 390px phone is readable instead of clipped"
    )


@pytest.mark.unit
def test_user_message_row_still_allows_the_text_to_shrink():
    """flex:1 without min-width:0 would keep the row as wide as the token."""
    body = _rule_body(_css(), ".user-message-row .message-text")
    assert re.search(r"min-width\s*:\s*0\b", body), (
        ".user-message-row .message-text needs min-width: 0 so the flex item "
        "can shrink below the unbreakable token's intrinsic width"
    )
