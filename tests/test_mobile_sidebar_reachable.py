"""Regression: conversation list / New Chat must stay reachable on phones.

An exploratory browser pass at 375×667 found the entire sidebar removed
from the accessibility tree (`@media (max-width: 768px) { .sidebar {
display: none; } }`) with no hamburger, and the advertised `/m` shortcut
404ing because the SPA catch-all mount was registered first.

These checks read source (no browser, network, or Docker) so they stay
hermetic. They lock the two contracts that made the controls unreachable.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _extract_media(css: str, query_needle: str) -> str:
    """Return the body of the first @media block whose prelude contains query_needle."""
    for match in re.finditer(r"@media\s*([^{]+)\{", css):
        if query_needle not in match.group(1):
            continue
        start = match.end()
        depth = 1
        i = start
        while i < len(css) and depth:
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
            i += 1
        return css[start : i - 1]
    return ""


def test_app_renders_sidebar_toggle():
    """Phone users need a .sidebar-toggle in the live markup, not just unused CSS."""
    app = (ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert re.search(r'className=["\']sidebar-toggle["\']', app), (
        "App.jsx must render a button with class sidebar-toggle so the "
        "conversation list can be opened at viewports ≤768px"
    )


def test_sidebar_still_has_new_chat():
    sidebar = (ROOT / "frontend" / "src" / "components" / "Sidebar.jsx").read_text(
        encoding="utf-8"
    )
    assert "new-chat-btn" in sidebar
    assert "ConversationHistory" in sidebar


def test_phone_breakpoint_can_show_sidebar():
    """The ≤768px sheet must not hide .sidebar with no way to bring it back."""
    css = (ROOT / "frontend" / "src" / "styles" / "responsive.css").read_text(
        encoding="utf-8"
    )
    mobile = _extract_media(css, "max-width: 768px")
    assert mobile, "responsive.css must define a max-width: 768px breakpoint"

    hide = re.search(
        r"(?<![\w-])\.sidebar\s*\{[^}]*display\s*:\s*none",
        mobile,
    )
    reveal = (
        re.search(r"\.sidebar-open\s+\.sidebar\s*\{", mobile)
        or re.search(r"\.sidebar\.open\s*\{", mobile)
        or re.search(
            r"\.sidebar(?:\.open)?\s*\{[^}]*display\s*:\s*(?:flex|block)",
            mobile,
        )
    )
    if hide and not reveal:
        pytest.fail(
            "@media (max-width: 768px) sets .sidebar { display: none } "
            "without an open/drawer rule (e.g. .app.sidebar-open .sidebar) "
            "to show New Chat, history, and Settings again"
        )


def test_mobile_shortcut_registered_before_spa_catch_all():
    """GET /m 404s in the built image if the '/' StaticFiles mount wins first."""
    src = (ROOT / "gateway" / "api.py").read_text(encoding="utf-8")
    m_idx = src.find('@app.get("/m")')
    spa_idx = src.find('app.mount("/",')
    assert m_idx != -1, "gateway must expose GET /m as the mobile-app shortcut"
    assert spa_idx != -1, "expected the SPA catch-all mount in gateway/api.py"
    assert m_idx < spa_idx, (
        "GET /m must be registered before app.mount('/', ...) or the "
        "built frontend swallows /m with a 404"
    )
