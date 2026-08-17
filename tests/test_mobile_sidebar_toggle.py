"""Regression: viewports under 768px must keep a way to open the sidebar.

A max-width:768px rule set ``.sidebar { display: none }`` and the React
shell rendered no hamburger / menu control, so New Chat, History, Settings,
Task Instructions, and the model picker were unreachable.  Reported
(explorer) at 375px: complementary landmark gone from the accessibility
tree, ``getComputedStyle(.sidebar).display === 'none'``, and
``querySelector('.sidebar-toggle, .menu-toggle, .hamburger')`` was null.
Only the example cards and the send button remained clickable.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend" / "src"
TOGGLE_RE = re.compile(r"\b(?:sidebar-toggle|menu-toggle|hamburger)\b")


def _read(rel: str) -> str:
    return (FRONTEND / rel).read_text(encoding="utf-8")


def _media_block(css: str, max_width: str) -> str:
    """Return the body of the first ``@media (max-width: N)`` block."""
    needle = f"@media (max-width: {max_width})"
    start = css.find(needle)
    if start < 0:
        pytest.fail(f"no {needle} rule in stylesheet")
    brace = css.find("{", start)
    depth = 0
    for idx, ch in enumerate(css[brace:], brace):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return css[brace + 1 : idx]
    pytest.fail(f"unclosed {needle} block")


def _declares_display(block: str, selector: str, value: str) -> bool:
    """True if ``selector { ... display: value ... }`` appears in ``block``."""
    pattern = re.compile(
        rf"{re.escape(selector)}\s*\{{[^}}]*display\s*:\s*{re.escape(value)}\b",
        re.DOTALL,
    )
    return bool(pattern.search(block))


def test_app_renders_menu_toggle_outside_sidebar():
    """The toggle must live in App chrome, not inside the hidden <aside>."""
    app = _read("App.jsx")
    sidebar = _read("components/Sidebar.jsx")
    assert TOGGLE_RE.search(app), (
        "App.jsx must render a .sidebar-toggle / .menu-toggle / .hamburger "
        "so the control stays in the DOM when CSS hides .sidebar"
    )
    assert not TOGGLE_RE.search(sidebar), (
        "do not put the only menu toggle inside Sidebar.jsx — "
        "display:none on .sidebar would hide it too"
    )


def test_narrow_viewport_shows_the_menu_toggle():
    """At max-width 768px the toggle must be display:flex (not left hidden)."""
    narrow = _media_block(_read("styles/responsive.css"), "768px")
    assert TOGGLE_RE.search(narrow), (
        "the 768px breakpoint must mention .sidebar-toggle (or menu-toggle / "
        "hamburger) so the control is visible after a desktop→phone resize"
    )
    assert _declares_display(narrow, ".sidebar-toggle", "flex") or _declares_display(
        narrow, ".sidebar-toggle", "block"
    ), "768px CSS must set .sidebar-toggle { display: flex } (or block)"
    # Desktop default lives in sidebar.css (outside any media query).
    desktop = _read("styles/sidebar.css")
    desktop_toggle = re.search(
        r"\.sidebar-toggle\s*\{[^}]*display\s*:\s*([^;]+)", desktop
    )
    assert desktop_toggle is not None
    assert desktop_toggle.group(1).strip() == "none", (
        "default .sidebar-toggle must be display:none so it only appears "
        "when the 768px rule turns it on"
    )


def test_narrow_viewport_can_reveal_hidden_sidebar():
    """Hiding .sidebar at 768px is fine only if an open-state rule undoes it."""
    narrow = _media_block(_read("styles/responsive.css"), "768px")
    assert _declares_display(narrow, ".sidebar", "none"), (
        "expected the reported hide rule to remain: .sidebar { display: none }"
    )
    assert _declares_display(narrow, ".app.sidebar-open .sidebar", "flex") or _declares_display(
        narrow, ".app.sidebar-open .sidebar", "block"
    ), (
        "768px CSS must show .sidebar again when .app has .sidebar-open "
        "(otherwise the hamburger has nothing to open)"
    )


def test_app_binds_sidebar_open_class_and_toggle_handler():
    """Clicking the toggle has to flip a class the 768px CSS can see."""
    app = _read("App.jsx")
    assert "sidebarOpen" in app
    assert "sidebar-open" in app
    assert re.search(r"setSidebarOpen", app)
    assert re.search(
        r"className=\{?[`'\"][^`'\"]*sidebar-toggle",
        app,
    ) or re.search(r'className="sidebar-toggle"', app)
