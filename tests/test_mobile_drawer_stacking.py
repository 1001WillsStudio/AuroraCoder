"""Regression: Task Instructions / History must stack above the mobile menu.

On a 390px phone the explorer opened the hamburger, then Task Instructions
(or All History). The drawer used z-index 200 while the slide-over sidebar
was 400 and the Close-menu backdrop was 350, so only a sliver of the panel
was tappable. elementFromPoint on the drawer X hit ``button.sidebar-backdrop``;
on the textarea it hit ``div.sidebar-actions``. Closing the menu also
dismissed the drawer (outside-click + History living inside the hidden
aside), so the instruction field could not be edited.

There is no JS test runner here, so this locks the CSS stacking contract
and the History portal by scanning source (hermetic: no network, no DOM).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend" / "src"


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


def _declared_z_index(block: str, selector: str) -> int:
    """First ``z-index: N`` in a rule whose selector list contains ``selector``."""
    pattern = re.compile(
        r"([^{}]+){\s*([^}]+)}",
        re.DOTALL,
    )
    for match in pattern.finditer(block):
        selectors = [part.strip() for part in match.group(1).split(",")]
        if selector not in selectors:
            continue
        z = re.search(r"z-index\s*:\s*(\d+)", match.group(2))
        if z:
            return int(z.group(1))
    pytest.fail(f"{selector} has no z-index in the given CSS block")


def test_narrow_viewport_drawers_stack_above_mobile_menu():
    """At 768px the drawers must paint above the sidebar and its backdrop."""
    narrow = _media_block(_read("styles/responsive.css"), "768px")
    sidebar_z = _declared_z_index(narrow, ".sidebar")
    backdrop_z = _declared_z_index(narrow, ".app.sidebar-open .sidebar-backdrop")
    history_z = _declared_z_index(narrow, ".history-drawer")
    task_z = _declared_z_index(narrow, ".task-instructions-drawer")

    assert sidebar_z == 400, "mobile sidebar stacking (explorer: 400) must stay"
    assert backdrop_z == 350, "Close-menu backdrop stacking (explorer: 350) must stay"
    assert history_z > sidebar_z and history_z > backdrop_z, (
        f".history-drawer z-index {history_z} must exceed sidebar {sidebar_z} "
        f"and backdrop {backdrop_z} so All History is tappable on a phone"
    )
    assert task_z > sidebar_z and task_z > backdrop_z, (
        f".task-instructions-drawer z-index {task_z} must exceed sidebar "
        f"{sidebar_z} and backdrop {backdrop_z} so the textarea and X are "
        "not covered by the mobile menu"
    )


def test_task_instructions_drawer_is_not_inside_the_sidebar():
    """Task Instructions is App chrome so hiding .sidebar cannot unmount it."""
    app = _read("App.jsx")
    sidebar = _read("components/Sidebar.jsx")
    assert "task-instructions-drawer" in app
    assert "task-instructions-drawer" not in sidebar


def test_history_drawer_portals_out_of_the_sidebar_stacking_context():
    """History is toggled from the aside but must paint outside its z-index 400."""
    history = _read("components/ConversationHistory.jsx")
    assert re.search(r"\bcreatePortal\b", history), (
        "HistoryDrawer must render through createPortal — a position:fixed "
        "child of .sidebar (z-index 400, overflow hidden, display none when "
        "the menu closes) stays trapped under the overlay or vanishes"
    )
    assert re.search(r"createPortal\s*\(\s*<HistoryDrawer\b", history) or re.search(
        r"createPortal\s*\([\s\S]*?className=\"history-drawer\"", history
    ), "the portaled node must be the History drawer, not an unrelated overlay"
    assert re.search(r"document\.body", history), (
        "portal target must be document.body so the drawer escapes .sidebar"
    )
