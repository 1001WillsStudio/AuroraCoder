"""Regression: user chat bubbles must keep typed line breaks.

Explorer (fresh page load): type ``First line of a list``, Shift+Enter,
``Second line of a list``, Enter to send. The composer kept the newline and
the user bubble's markup was ``<p>First line of a list\\nSecond line of a
list</p>``, but CSS ``white-space: normal`` collapsed it to one line
(innerText single line, bubble height ~27px).

There is no JS test runner here, so this locks the contract in source:
  * the user path still interpolates the raw string inside ``.message-text``
    (so the newline stays in the text node);
  * ``messages.css`` sets ``white-space: pre-wrap`` on that bubble so the
    break is visible (same treatment as the task-instruction chip).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend" / "src"

# Selector that actually paints the sent user bubble (not the chip).
_USER_BUBBLE_WHITESPACE = re.compile(
    r"\.user-message(?:-row)?\s+\.message-text(?:\s+p)?\s*\{[^}]*"
    r"white-space\s*:\s*pre-wrap\b",
    re.DOTALL,
)


def _read(rel: str) -> str:
    return (FRONTEND / rel).read_text(encoding="utf-8")


@pytest.mark.unit
def test_user_bubble_renders_raw_content_with_newlines_intact():
    """``{message.content}`` must stay a text child of the user <p>.

    Markdown would treat a single ``\\n`` as a space; splitting or replacing
    newlines here would also hide what the composer sent.
    """
    src = _read("components/ChatMessage.jsx")
    # The sent bubble lives in .user-message-row (fork button sits beside it).
    row = re.search(
        r'<div className="user-message-row">(?P<body>.*?)</div>\s*\{/\*',
        src,
        re.DOTALL,
    )
    assert row, "user bubble must render inside .user-message-row"
    body = row.group("body")
    assert 'className="message-text"' in body
    assert "<p>{message.content}</p>" in body
    assert "ReactMarkdown" not in body
    assert "replace(" not in body
    assert ".split(" not in body


@pytest.mark.unit
def test_user_bubble_css_preserves_line_breaks():
    css = _read("styles/messages.css")
    assert _USER_BUBBLE_WHITESPACE.search(css), (
        "user bubble .message-text must set white-space: pre-wrap so a "
        "newline inside <p> is not collapsed (white-space: normal)"
    )
    # The chip already has pre-wrap; that must not be the only hit.
    chip = re.search(
        r"\.task-instruction-chip-text\s*\{[^}]*white-space\s*:\s*pre-wrap\b",
        css,
        re.DOTALL,
    )
    bubble = _USER_BUBBLE_WHITESPACE.search(css)
    assert chip is not None
    assert bubble is not None
    assert chip.start() != bubble.start()
