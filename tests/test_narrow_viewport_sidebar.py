"""Regression: workspace file controls stay reachable below 768px.

Explorer finding t-3e798b5881: a 375×844 load hid the entire sidebar with
``@media (max-width: 768px) { .sidebar { display: none; } }`` and provided
no hamburger/drawer, so Upload Project, the file tree, and download/export
were unreachable.

Hermetic source-contract test — no browser, network, or DOM.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_SRC = ROOT / "frontend" / "src"

# A phone-sized width used in the original report.
NARROW_PX = 375


def _css_text() -> str:
    styles = FRONTEND_SRC / "styles"
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(styles.glob("*.css")))


def _jsx_text() -> str:
    parts = [(FRONTEND_SRC / "App.jsx").read_text(encoding="utf-8")]
    components = FRONTEND_SRC / "components"
    parts.extend(p.read_text(encoding="utf-8") for p in sorted(components.glob("*.jsx")))
    return "\n".join(parts)


def _extract_balanced_block(text: str, open_brace_idx: int) -> str:
    depth = 0
    for i, ch in enumerate(text[open_brace_idx:], start=open_brace_idx):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_idx + 1 : i]
    raise AssertionError("unbalanced CSS braces while parsing media queries")


def _max_width_px(query: str) -> int | None:
    m = re.search(r"max-width\s*:\s*(\d+)px", query, re.I)
    return int(m.group(1)) if m else None


def _rules_in(block: str) -> list[tuple[str, str]]:
    stripped = re.sub(r"@[^{]+\{(?:[^{}]|\{[^{}]*\})*\}", "", block)
    return [
        (m.group(1).strip(), m.group(2))
        for m in re.finditer(r"([^{}]+)\{([^{}]+)\}", stripped)
    ]


def _decl_value(decls: str, prop: str) -> str | None:
    m = re.search(rf"(?:^|;)\s*{re.escape(prop)}\s*:\s*([^;]+)", decls, re.I)
    return m.group(1).strip() if m else None


def _last_simple_selector(part: str) -> str:
    return part.strip().split()[-1] if part.strip() else ""


def _targets_sidebar(selector: str) -> bool:
    for part in selector.split(","):
        last = _last_simple_selector(part)
        if re.search(r"(^|\.)sidebar($|[.#:[])", last):
            return True
    return False


def _is_open_variant(selector: str) -> bool:
    return bool(
        re.search(
            r"sidebar-open|mobile-open|mobile-sidebar|\.open\b|sidebar\.open",
            selector,
            re.I,
        )
    )


def _mobile_sidebar_display_rules() -> list[tuple[str, str]]:
    """``(selector, display)`` for ``.sidebar`` in media queries that apply at 375px."""
    css = _css_text()
    found: list[tuple[str, str]] = []
    for m in re.finditer(r"@media\s*\(([^)]*)\)\s*\{", css, re.I):
        max_w = _max_width_px(m.group(1))
        if max_w is None or max_w < NARROW_PX:
            continue
        body = _extract_balanced_block(css, m.end() - 1)
        for selector, decls in _rules_in(body):
            if not _targets_sidebar(selector):
                continue
            display = _decl_value(decls, "display")
            if display:
                found.append((selector, display))
    return found


def _has_sidebar_menu_control(jsx: str) -> bool:
    return bool(
        re.search(
            r"mobile-sidebar-toggle|sidebar-menu-btn|className=\{?['\"]sidebar-toggle['\"]",
            jsx,
        )
    )


@pytest.mark.unit
def test_upload_and_file_tree_live_in_sidebar():
    """Revealing the sidebar must reveal upload + workspace actions."""
    sidebar = (FRONTEND_SRC / "components" / "Sidebar.jsx").read_text(encoding="utf-8")
    tree = (FRONTEND_SRC / "components" / "FileTree.jsx").read_text(encoding="utf-8")
    assert "sidebar.uploadProject" in sidebar
    assert "FileTree" in sidebar
    assert "fileTree.download" in tree
    assert "fileTree.export" in tree


@pytest.mark.unit
def test_sidebar_reachable_on_narrow_viewport():
    """At 375px the sidebar must stay reachable — visible, or hidden behind a menu."""
    rules = _mobile_sidebar_display_rules()
    hide = [(s, v) for s, v in rules if v == "none" and not _is_open_variant(s)]
    restore = [(s, v) for s, v in rules if v != "none" and _is_open_variant(s)]
    jsx = _jsx_text()

    if not hide:
        return

    assert restore, (
        "CSS hides .sidebar at max-width 768px "
        f"({hide}) but no open/menu class restores display. "
        "Narrow viewports then lose Upload Project, the file tree, "
        "and download/export with no way to get them back."
    )
    assert _has_sidebar_menu_control(jsx), (
        "Sidebar is hidden on narrow viewports but no hamburger/menu "
        "control (mobile-sidebar-toggle) exists to open it."
    )
