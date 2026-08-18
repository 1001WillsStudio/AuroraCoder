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


def _declares_prop(block: str, selector: str, prop: str, value: str) -> bool:
    """True if ``selector { ... prop: value ... }`` appears in ``block``."""
    pattern = re.compile(
        rf"{re.escape(selector)}\s*\{{[^}}]*{re.escape(prop)}\s*:\s*{re.escape(value)}\b",
        re.DOTALL,
    )
    return bool(pattern.search(block))


_FEATURE_RE = re.compile(r"\((min|max)-(width|height)\s*:\s*(\d+)px\)", re.I)

# iPhone 14 CSS landscape; 16% of 844px is ~135px (the crushed desktop sidebar).
IPHONE_14_LANDSCAPE = (844, 390)
PHONE_PORTRAIT = (375, 812)
DESKTOP = (1280, 800)


def _iter_media_blocks(css: str):
    """Yield ``(query, body)`` for each ``@media`` rule in ``css``."""
    for match in re.finditer(r"@media\s*(.*?)\s*\{", css, re.DOTALL):
        query = " ".join(match.group(1).split())
        brace = match.end() - 1
        depth = 0
        for idx, ch in enumerate(css[brace:], brace):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield query, css[brace + 1 : idx]
                    break


def _clause_matches(clause: str, width: int, height: int) -> bool:
    features = _FEATURE_RE.findall(clause)
    if not features:
        return False
    dims = {"width": width, "height": height}
    for bound, axis, raw in features:
        actual = dims[axis.lower()]
        value = int(raw)
        if bound.lower() == "max" and actual > value:
            return False
        if bound.lower() == "min" and actual < value:
            return False
    return True


def _query_matches(query: str, width: int, height: int) -> bool:
    """Comma-separated ``@media`` alternatives (OR); ``and`` features inside each."""
    return any(_clause_matches(part, width, height) for part in query.split(","))


def _hamburger_drawer_rule(css: str) -> tuple[str, str]:
    """The ``@media`` block that reveals ``.sidebar-toggle`` (hamburger / drawer)."""
    for query, body in _iter_media_blocks(css):
        if _declares_display(body, ".sidebar-toggle", "flex") or _declares_display(
            body, ".sidebar-toggle", "block"
        ):
            return query, body
    pytest.fail("no @media block sets .sidebar-toggle { display: flex|block }")


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


def test_phone_landscape_uses_hamburger_drawer():
    """844x390 is wider than 768px, so a width-only breakpoint treats it as
    desktop: ``--sidebar-width: 16%`` → ~135px, brand clipped to 0, theme and
    Settings overlapping the logo, hamburger ``display:none``. History sits at
    y=390 and Model at y=475 — both below the 390px viewport.
    """
    css = _read("styles/responsive.css")
    query, body = _hamburger_drawer_rule(css)
    assert _query_matches(query, *IPHONE_14_LANDSCAPE), (
        "viewport 844x390 (phone landscape) must match the hamburger/drawer "
        f"media query; got {query!r} which does not apply, so the 16% desktop "
        "sidebar (~135px, overflow:hidden) stays and History/Model are "
        "off-screen and unscrollable"
    )
    assert _query_matches(query, *PHONE_PORTRAIT), (
        "phone portrait 375x812 must keep the existing hamburger drawer"
    )
    assert not _query_matches(query, *DESKTOP), (
        "desktop 1280x800 must keep the persistent sidebar (no hamburger)"
    )
    assert _declares_display(body, ".sidebar", "none")
    assert _declares_display(body, ".app.sidebar-open .sidebar", "flex") or _declares_display(
        body, ".app.sidebar-open .sidebar", "block"
    )


def test_phone_landscape_drawer_can_scroll_to_history_and_model():
    """Even as a drawer, a 390px-tall landscape viewport is shorter than the
    sidebar content (~529px). ``overflow: hidden`` would still leave All
    History and the Model picker below the fold after opening the menu.
    """
    css = _read("styles/responsive.css")
    _query, body = _hamburger_drawer_rule(css)
    assert _declares_prop(body, ".sidebar", "overflow-y", "auto") or _declares_prop(
        body, ".sidebar", "overflow-y", "scroll"
    ), (
        "the hamburger/drawer @media rule must set .sidebar { overflow-y: auto } "
        "so History and Model can be scrolled into view on a 390px-tall phone"
    )
