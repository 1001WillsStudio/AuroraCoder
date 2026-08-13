"""Regression: phone-width viewports must keep sidebar actions reachable.

The desktop React UI hides `.sidebar` at `@media (max-width: 768px)`. The
exploratory browser agent found that this was a permanent `display: none`
with no hamburger/menu control, so New Chat, All History, Settings, Task
Instructions, and the model picker vanished from the accessibility tree
at 375×667.

These tests lock the source contract (hermetic: they read frontend files,
they do not boot a browser or touch the network):

1. The 768px breakpoint must not hide `.sidebar` unless an open state
   (`.sidebar.mobile-open`) restores a visible `display`.
2. A `.mobile-menu-btn` must exist in the React tree and be shown at that
   breakpoint so the drawer can be opened.
3. The sidebar must still contain the core actions (new chat, settings,
   history trigger).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend" / "src"
RESPONSIVE_CSS = FRONTEND / "styles" / "responsive.css"
APP_JSX = FRONTEND / "App.jsx"
SIDEBAR_JSX = FRONTEND / "components" / "Sidebar.jsx"
HISTORY_JSX = FRONTEND / "components" / "ConversationHistory.jsx"


def _media_block(css: str, max_width: str) -> str:
    """Return the body of `@media (max-width: <max_width>)`, brace-matched."""
    pattern = rf"@media\s*\(\s*max-width\s*:\s*{re.escape(max_width)}\s*\)"
    match = re.search(pattern, css)
    if not match:
        pytest.fail(f"no @media (max-width: {max_width}) rule in responsive.css")
    brace = css.find("{", match.end())
    if brace < 0:
        pytest.fail(f"unopened @media (max-width: {max_width}) rule")
    depth = 0
    for i, ch in enumerate(css[brace:], start=brace):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return css[brace + 1 : i]
    pytest.fail(f"unclosed @media (max-width: {max_width}) rule")


def _rule_body(block: str, selector: str) -> str | None:
    """First `{...}` body for `selector` in `block`, ignoring nested braces."""
    pattern = rf"{re.escape(selector)}\s*\{{"
    match = re.search(pattern, block)
    if not match:
        return None
    start = match.end() - 1
    depth = 0
    for i, ch in enumerate(block[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return block[start + 1 : i]
    return None


def _declared_display(rule_body: str | None) -> str | None:
    if not rule_body:
        return None
    match = re.search(r"display\s*:\s*([a-z-]+)", rule_body)
    return match.group(1) if match else None


def test_768px_sidebar_hide_has_openable_drawer():
    """Bare `.sidebar { display: none }` at phone width is the reported bug.

    It is allowed only when `.sidebar.mobile-open` restores a visible display
    (flex/block/grid) so a menu button can reopen New Chat / History / Settings.
    """
    css = RESPONSIVE_CSS.read_text(encoding="utf-8")
    block = _media_block(css, "768px")

    sidebar_display = _declared_display(_rule_body(block, ".sidebar"))
    open_display = _declared_display(_rule_body(block, ".sidebar.mobile-open"))

    if sidebar_display == "none":
        assert open_display in {"flex", "block", "grid", "inline-flex"}, (
            "at max-width 768px .sidebar is display:none with no .sidebar.mobile-open "
            "override; New Chat / History / Settings become unreachable"
        )


def test_mobile_menu_button_is_rendered_and_shown_at_768px():
    """A hamburger/menu control must exist and be visible at phone width."""
    app = APP_JSX.read_text(encoding="utf-8")
    css = RESPONSIVE_CSS.read_text(encoding="utf-8")
    block = _media_block(css, "768px")

    assert 'className="mobile-menu-btn"' in app or "className='mobile-menu-btn'" in app, (
        "App.jsx has no .mobile-menu-btn; at phone width there is nothing to reopen the sidebar"
    )
    assert "setMobileSidebarOpen" in app or "mobileSidebarOpen" in app
    assert "sidebar.openMenu" in app or "aria-label" in app

    btn_display = _declared_display(_rule_body(block, ".mobile-menu-btn"))
    assert btn_display in {"flex", "block", "inline-flex", "grid"}, (
        "at max-width 768px .mobile-menu-btn is not displayed, so the drawer cannot be opened"
    )


def test_sidebar_still_hosts_new_chat_history_and_settings():
    """The drawer that the menu opens must still contain the core actions."""
    sidebar = SIDEBAR_JSX.read_text(encoding="utf-8")
    history = HISTORY_JSX.read_text(encoding="utf-8")

    assert "new-chat-btn" in sidebar
    assert "settings-gear-btn" in sidebar
    assert "onOpenSettings" in sidebar
    assert "history-trigger" in history
    assert "mobile-open" in sidebar
