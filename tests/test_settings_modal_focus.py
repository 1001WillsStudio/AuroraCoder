"""Regression: Settings modal must take and keep keyboard focus.

Explorer (fresh page load, 1440×900): click Settings (gear), then Tab,
then Tab again. Focus stayed on ``[data-testid=settings-button]``; the
first Tab landed on + New Chat (``[data-testid=new-conversation]``) and
the second on Upload Project — both in the sidebar behind the dimmed
overlay. The overlay was a plain ``div``: no ``role=dialog``, no
``aria-modal``, no focus move, no Tab trap.

There is no JS test runner here, so behaviour is locked two ways:
  * helpers in ``frontend/src/utils/focusTrap.js`` run under Node
    (hermetic: a fake dialog/sidebar tree, no network, no browser);
  * a source scan asserts the settings panel is a modal dialog and
    SettingsPanel moves focus in, traps Tab, and restores the gear.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "frontend" / "src" / "utils" / "focusTrap.js"
SETTINGS = ROOT / "frontend" / "src" / "components" / "SettingsPanel.jsx"
SIDEBAR = ROOT / "frontend" / "src" / "components" / "Sidebar.jsx"

# Opening-tag innards may span lines and contain `=>` arrow functions.
_TAG_ATTRS = r"(?:=>|[^>])*"


def _eval_js(expr: str):
    script = (
        "import {\n"
        "  getFocusableElements,\n"
        "  initialFocusTarget,\n"
        "  moveFocus,\n"
        "  trapTabKey,\n"
        "  wrapTabTarget,\n"
        f"}} from {json.dumps(HELPER.resolve().as_uri())}\n"
        # Fake a sidebar-behind-overlay + settings dialog the way the
        # explorer walked it: gear still focused, then Tab / Tab.
        "function el(id) {\n"
        "  return {\n"
        "    id,\n"
        "    disabled: false,\n"
        "    hidden: false,\n"
        "    focused: false,\n"
        "    focus() { this.focused = true; globalThis.__active = this },\n"
        "    getAttribute() { return null },\n"
        "  }\n"
        "}\n"
        "const settingsButton = el('settings-button')\n"
        "const newChat = el('new-conversation')\n"
        "const upload = el('upload')\n"
        "const lang = el('language')\n"
        "const closeBtn = el('close')\n"
        "const cancel = el('cancel')\n"
        "const save = el('save')\n"
        "const dialogEls = [lang, closeBtn, cancel, save]\n"
        "const dialog = {\n"
        "  querySelectorAll() { return dialogEls },\n"
        "  contains(node) { return dialogEls.includes(node) },\n"
        "  focus() { this.focused = true; globalThis.__active = this },\n"
        "}\n"
        "const documentOrder = [settingsButton, newChat, upload, ...dialogEls]\n"
        "function untappedNext(cur) {\n"
        "  return documentOrder[documentOrder.indexOf(cur) + 1]\n"
        "}\n"
        "function tabEvent(target, shiftKey) {\n"
        "  return {\n"
        "    key: 'Tab', target, shiftKey: !!shiftKey,\n"
        "    preventDefault() { this.prevented = true },\n"
        "  }\n"
        "}\n"
        f"const out = {expr}\n"
        "console.log(JSON.stringify(out))\n"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout or "node helper failed")
    return json.loads(proc.stdout)


@pytest.mark.unit
def test_untapped_tab_order_reproduces_the_explorer_finding():
    """Without a trap, Tab from the gear lands on + New Chat, then Upload."""
    out = _eval_js(
        """(() => {
          const first = untappedNext(settingsButton)
          const second = untappedNext(first)
          return { first: first.id, second: second.id }
        })()"""
    )
    assert out["first"] == "new-conversation"
    assert out["second"] == "upload"


@pytest.mark.unit
def test_opening_settings_moves_focus_into_the_dialog():
    """Reproduce: after the gear click, activeElement was the gear.

    Opening must move focus to the first control inside the dialog
    (language selector), not leave it on settings-button.
    """
    out = _eval_js(
        """(() => {
          globalThis.__active = settingsButton
          moveFocus(initialFocusTarget(dialog))
          return { active: globalThis.__active.id }
        })()"""
    )
    assert out["active"] == "language"


@pytest.mark.unit
def test_tab_from_the_gear_enters_the_dialog_not_new_chat():
    """If the gear is still focused, the first Tab must not reach + New Chat."""
    out = _eval_js(
        """(() => {
          globalThis.__active = settingsButton
          const ev = tabEvent(settingsButton, false)
          const trapped = trapTabKey(ev, dialog)
          return {
            trapped,
            prevented: !!ev.prevented,
            active: globalThis.__active.id,
          }
        })()"""
    )
    assert out["trapped"] is True
    assert out["prevented"] is True
    assert out["active"] == "language"


@pytest.mark.unit
def test_tab_wraps_inside_the_dialog():
    """Tab on the last control and Shift+Tab on the first stay in the dialog."""
    out = _eval_js(
        """(() => {
          const forward = tabEvent(save, false)
          trapTabKey(forward, dialog)
          const afterForward = globalThis.__active.id
          const back = tabEvent(lang, true)
          trapTabKey(back, dialog)
          return {
            afterForward,
            afterBack: globalThis.__active.id,
            wrapForward: wrapTabTarget(dialogEls, save, false).id,
            wrapBack: wrapTabTarget(dialogEls, lang, true).id,
            mid: wrapTabTarget(dialogEls, closeBtn, false),
          }
        })()"""
    )
    assert out["afterForward"] == "language"
    assert out["afterBack"] == "save"
    assert out["wrapForward"] == "language"
    assert out["wrapBack"] == "save"
    assert out["mid"] is None


@pytest.mark.unit
def test_get_focusable_skips_disabled_and_hidden():
    out = _eval_js(
        """(() => {
          const disabled = el('disabled'); disabled.disabled = true
          const hidden = el('hidden'); hidden.hidden = true
          const ok = el('ok')
          const root = {
            querySelectorAll() { return [disabled, hidden, ok] },
          }
          return getFocusableElements(root).map(e => e.id)
        })()"""
    )
    assert out == ["ok"]


@pytest.mark.unit
def test_settings_overlay_is_a_modal_dialog():
    """The explorer found no role=dialog or aria-modal on the overlay."""
    src = SETTINGS.read_text(encoding="utf-8")
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
def test_settings_panel_traps_tab_and_restores_focus():
    src = SETTINGS.read_text(encoding="utf-8")
    assert "from '../utils/focusTrap.js'" in src or 'from "../utils/focusTrap.js"' in src
    assert "trapTabKey" in src
    assert "initialFocusTarget" in src
    assert "moveFocus" in src
    assert re.search(r"document\.activeElement", src)
    assert re.search(r"addEventListener\(\s*['\"]keydown['\"]", src)
    # Cleanup must put focus back on the gear (or whoever opened it).
    cleanup = src.split("addEventListener", 1)[1]
    assert "moveFocus" in cleanup
    assert "removeEventListener" in cleanup


@pytest.mark.unit
def test_sidebar_document_order_puts_new_chat_after_the_gear():
    """Locks the reported tab order behind the overlay (why the trap exists)."""
    src = SIDEBAR.read_text(encoding="utf-8")
    gear = src.find('data-testid="settings-button"')
    new_chat = src.find('data-testid="new-conversation"')
    upload = src.find("sidebar.uploadProject")
    assert 0 <= gear < new_chat < upload
