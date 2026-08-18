"""Regression: short phone viewports must not clip the welcome screen.

On 375x667 (iPhone SE) ``.welcome-screen`` used ``height: 100%`` plus
``justify-content: center`` inside a shorter ``.chat-container``. The
centered column overflowed equally off both ends: the AuroraCoder title
sat above the scroll origin (h1 top ≈ -68px, ``scrollTop`` already 0) and
the last example card painted under the composer (taps hit the textarea).
Landscape 667x375 dropped the title, description, and two of four cards.

The box must be allowed to grow with its content so ``.chat-container``
(overflow-y: auto) can scroll from the title down through every card.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend" / "src"


def _read(rel: str) -> str:
    return (FRONTEND / rel).read_text(encoding="utf-8")


def _rule_block(css: str, selector: str) -> str:
    """Return the body of the first top-level ``selector { ... }`` rule."""
    pattern = re.compile(rf"{re.escape(selector)}\s*\{{", re.MULTILINE)
    match = pattern.search(css)
    if not match:
        pytest.fail(f"no {selector!r} rule in stylesheet")
    brace = match.end() - 1
    depth = 0
    for idx, ch in enumerate(css[brace:], brace):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return css[brace + 1 : idx]
    pytest.fail(f"unclosed {selector!r} block")


def _decls(block: str, prop: str) -> list[str]:
    return [
        m.group(1).strip()
        for m in re.finditer(
            rf"^\s*{re.escape(prop)}\s*:\s*([^;]+);",
            block,
            re.MULTILINE,
        )
    ]


def _media_block(css: str, query_substr: str) -> str:
    start = css.find(f"@media ({query_substr})")
    if start < 0:
        pytest.fail(f"no @media ({query_substr}) rule in stylesheet")
    brace = css.find("{", start)
    depth = 0
    for idx, ch in enumerate(css[brace:], brace):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return css[brace + 1 : idx]
    pytest.fail(f"unclosed @media ({query_substr}) block")


def test_welcome_screen_grows_with_content_instead_of_clipping():
    """height:100% + flex-center puts the title above the scroll origin."""
    block = _rule_block(_read("styles/welcome.css"), ".welcome-screen")

    min_heights = _decls(block, "min-height")
    assert any(v == "100%" for v in min_heights), (
        ".welcome-screen must use min-height: 100% so a short chat pane "
        "still centers, but a tall column (logo + title + four cards) can "
        "grow and be scrolled inside .chat-container"
    )
    flex_vals = _decls(block, "flex")
    assert any(re.match(r"1\s+0\s+auto$", v) for v in flex_vals), (
        ".welcome-screen must be flex: 1 0 auto so it fills leftover chat "
        "height but cannot shrink below the title + example cards"
    )

    for value in _decls(block, "height"):
        assert value != "100%", (
            "height: 100% locks the welcome box to the chat pane; "
            "justify-content: center then overflows the title above y=0 "
            "where scrollTop cannot reach it"
        )


def test_chat_container_remains_the_welcome_scroll_parent():
    """Grown welcome content is useless if the chat pane cannot scroll."""
    layout = _read("styles/layout.css")
    chat = _rule_block(layout, ".chat-container")
    overflow_y = _decls(chat, "overflow-y")
    assert overflow_y and overflow_y[-1] == "auto", (
        ".chat-container must keep overflow-y: auto so the title and "
        "example cards can be scrolled into view on a short phone"
    )
    assert "flex" in _decls(chat, "display") and "column" in " ".join(
        _decls(chat, "flex-direction")
    ), (
        ".chat-container must be a column flex container so the welcome "
        "screen's flex-grow fills a short pane without a locked height"
    )
    welcome = _rule_block(_read("styles/welcome.css"), ".welcome-screen")
    overflow = _decls(welcome, "overflow") + _decls(welcome, "overflow-y")
    assert not any(v == "hidden" for v in overflow), (
        ".welcome-screen must not clip its own overflow — the chat pane "
        "is the scrollport"
    )


def test_short_phone_welcome_does_not_reintroduce_fixed_height():
    """Narrow / short breakpoints must not set height: 100% again."""
    responsive = _read("styles/responsive.css")
    for query in ("max-width: 768px", "max-height: 500px"):
        block = _media_block(responsive, query)
        nested = re.search(
            r"\.welcome-screen\s*\{([^}]*)\}",
            block,
            re.DOTALL,
        )
        assert nested is not None, (
            f"{query} must restyle .welcome-screen (compact padding / "
            "no height: 100%) so landscape phones can reach the title"
        )
        for value in _decls(nested.group(1), "height"):
            assert value != "100%", (
                f"{query} must not restore height: 100% on .welcome-screen"
            )
