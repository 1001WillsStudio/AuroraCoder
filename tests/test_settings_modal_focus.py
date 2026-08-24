"""Regression: Settings modal must take and keep keyboard focus.

Explorer (fresh page load, 1440×900): click Settings (gear), then Tab,
then Tab again. Focus stayed on ``[data-testid=settings-button]``; the
first Tab landed on + New Chat (``[data-testid=new-conversation]``) and
the second on Upload Project — both in the sidebar behind the dimmed
overlay. The overlay was a plain ``div``: no ``role=dialog``, no
``aria-modal``, no focus move, no Tab trap.

There is no JS test runner here, so this locks the contract in source:
  * the settings panel is a modal dialog;
  * opening focuses the first tabbable control inside it;
  * Tab at the edges wraps inside the dialog (so + New Chat / Upload
    behind the overlay cannot be reached);
  * the sidebar still puts New Chat after the gear (why the trap exists).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "frontend" / "src" / "components" / "SettingsPanel.jsx"
SIDEBAR = ROOT / "frontend" / "src" / "components" / "Sidebar.jsx"
HELPER = ROOT / "frontend" / "src" / "utils" / "focusTrap.js"

# Opening-tag innards may span lines and contain `=>` arrow functions.
_TAG_ATTRS = r"(?:=>|[^>])*"


def _settings_src() -> str:
    return SETTINGS.read_text(encoding="utf-8")


def _focus_effect(src: str) -> str:
    """The open-dialog effect that moves focus and traps Tab."""
    start = src.find("const dialogRef = useRef")
    end = src.find("const handleDeleteMemory")
    assert start != -1 and end != -1 and start < end, (
        "SettingsPanel must keep the dialog focus effect next to dialogRef"
    )
    return src[start:end]


@pytest.mark.unit
def test_settings_overlay_is_a_modal_dialog():
    """The explorer found no role=dialog or aria-modal on the overlay."""
    src = _settings_src()
    dialog = re.search(
        rf"<div\b{_TAG_ATTRS}data-testid=\"settings-panel\"",
        src,
        flags=re.DOTALL,
    )
    assert dialog, "settings-panel host element is missing"
    tag = dialog.group(0)
    assert re.search(r'\brole=["\']dialog["\']', tag), (
        "settings-panel must be role=dialog so assistive tech treats it "
        "as a modal, not a plain overlay"
    )
    assert re.search(r'\baria-modal=["\']true["\']', tag), (
        "settings-panel must set aria-modal=true so the background is "
        "announced as inert"
    )


@pytest.mark.unit
def test_opening_settings_moves_focus_into_the_dialog():
    """Reproduce: after the gear click, activeElement was the gear.

    Opening must focus the first tabbable control inside the dialog
    (the language selector), not leave it on settings-button.
    """
    effect = _focus_effect(_settings_src())
    assert "document.activeElement" in effect
    assert "querySelectorAll" in effect
    assert "select:not([disabled])" in effect
    assert re.search(r"tabbable\(\)\[0\]", effect)
    assert re.search(r"\.focus\(\)", effect)
    assert "addEventListener" in effect


@pytest.mark.unit
def test_tab_wraps_inside_the_dialog_not_new_chat():
    """Tab at the last control (or from the gear) must stay in the dialog."""
    effect = _focus_effect(_settings_src())
    assert re.search(r"event\.key\s*!==\s*['\"]Tab['\"]", effect)
    assert "preventDefault" in effect
    assert "firstEl" in effect and "lastEl" in effect
    assert "dialog.contains" in effect
    # Wrap: Tab on last → first; Shift+Tab on first → last.
    assert re.search(r"event\.target\s*===\s*lastEl", effect)
    assert re.search(r"event\.target\s*===\s*firstEl", effect)
    assert re.search(r"\(event\.shiftKey\s*\?\s*lastEl\s*:\s*firstEl\)\.focus\(\)", effect)


@pytest.mark.unit
def test_settings_focus_change_stays_minimal():
    """No Escape-to-close and no shared focusTrap helper — those were extra."""
    src = _settings_src()
    effect = _focus_effect(src)
    assert "Escape" not in effect
    assert "focusTrap" not in src
    assert not HELPER.exists(), "do not reintroduce frontend/src/utils/focusTrap.js"


@pytest.mark.unit
def test_sidebar_document_order_puts_new_chat_after_the_gear():
    """Without a trap, Tab from the gear lands on + New Chat, then Upload."""
    src = SIDEBAR.read_text(encoding="utf-8")
    gear = src.find('data-testid="settings-button"')
    new_chat = src.find('data-testid="new-conversation"')
    upload = src.find("sidebar.uploadProject")
    assert 0 <= gear < new_chat < upload
