"""Regression: Workspace file/folder rows must be reachable from the keyboard.

Explorer (fresh page load, desktop width): click the top of the page and Tab
through Theme → Settings → New Chat → Upload Project → Task Instructions →
Refresh file tree → All History → Model → example cards → chat input. Sixteen
Tabs never focused a ``.tree-item``. Every row was a DIV with computed
``tabIndex: -1`` and no ``role``, so Enter could not expand a folder, open a
file, or reach Download / Export / Delete.

There is no JS test runner here, so behaviour is locked two ways:
  * helpers in ``frontend/src/utils/treeKeyboard.js`` run via Node (hermetic:
    no network, no DOM);
  * a source scan asserts rows are real buttons (in the tab order), show a
    focus ring, and expose a keyboard path to the existing context menu.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "frontend" / "src" / "utils" / "treeKeyboard.js"
TREE = ROOT / "frontend" / "src" / "components" / "FileTree.jsx"
CSS = ROOT / "frontend" / "src" / "styles" / "file-tree.css"


def _tree() -> str:
    return TREE.read_text(encoding="utf-8")


def _css() -> str:
    return CSS.read_text(encoding="utf-8")


def _tree_node_src() -> str:
    src = _tree()
    start = src.index("function TreeNode")
    end = src.index("// ── Right-click context menu")
    return src[start:end]


def _eval_js(expr: str):
    script = (
        "import {\n"
        "  isTreeContextMenuKey,\n"
        "  menuCoordsFromRect,\n"
        f"}} from {json.dumps(HELPER.resolve().as_uri())}\n"
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
def test_tree_item_is_a_button_not_a_plain_div():
    """The reported DOM: ``div.tree-item`` with no role and tabIndex -1."""
    node = _tree_node_src()
    assert re.search(
        r"<button\b[^>]*className=\{`tree-item",
        node,
        flags=re.DOTALL,
    ), "each file/folder row must be a <button className={`tree-item ...`}> so Tab can land on it"
    assert 'type="button"' in node
    # The old markup was a non-interactive div; do not regress to that.
    assert not re.search(
        r"<div\b[^>]*className=\{`tree-item",
        node,
        flags=re.DOTALL,
    ), "tree-item must not be a plain div — those stay out of the tab order"


@pytest.mark.unit
def test_tree_item_stays_in_the_tab_order():
    node = _tree_node_src()
    assert "tabIndex={-1}" not in node
    assert "tabIndex={ -1 }" not in node
    assert 'tabIndex="-1"' not in node


@pytest.mark.unit
def test_enter_on_a_focused_row_uses_native_button_activation():
    """Enter / Space on a focused <button> fires onClick — folders expand, files open."""
    node = _tree_node_src()
    click_fn = node[node.index("const handleClick") : node.index("return (")]
    assert "toggleFolder" in click_fn
    assert "onFileClick" in click_fn
    button = re.search(
        r"<button\b(?:=>|[^>])*className=\{`tree-item(?:=>|[^>])*>",
        node,
        flags=re.DOTALL,
    )
    assert button, "tree-item button opening tag not found"
    assert "onClick={handleClick}" in button.group(0)


@pytest.mark.unit
def test_tree_item_has_a_visible_focus_ring():
    css = _css()
    assert re.search(
        r"\.tree-item:focus-visible\s*\{[^}]*outline\s*:",
        css,
        flags=re.DOTALL,
    ), "keyboard focus on a Workspace row must show an outline (focus ring)"


@pytest.mark.unit
def test_keyboard_can_open_download_export_delete_menu():
    """Shift+F10 / ContextMenu, or the per-row actions button, must open the menu."""
    src = _tree()
    node = _tree_node_src()
    assert "isTreeContextMenuKey" in node
    assert "onKeyDown" in node
    assert re.search(
        r"<button\b[^>]*className=\"tree-item-menu\"",
        node,
        flags=re.DOTALL,
    ), "a focusable actions control must sit beside each row so Tab can reach Download/Export/Delete"
    assert "aria-haspopup" in node
    # Menu entries stay real buttons (already were); lock that they remain so.
    assert src.count('className="context-menu-item') >= 2
    assert "<button className=\"context-menu-item\"" in src
    assert "<button className=\"context-menu-item danger\"" in src


@pytest.mark.unit
def test_context_menu_moves_focus_to_the_first_action():
    src = _tree()
    start = src.index("function ContextMenu")
    menu = src[start : src.index("// ── Main component")]
    assert "focus()" in menu, (
        "opening the menu from the keyboard must move focus onto Download / Export / Delete"
    )


@pytest.mark.unit
def test_context_menu_key_helper_matches_platform_shortcuts():
    assert _eval_js("isTreeContextMenuKey({ key: 'ContextMenu', shiftKey: false })") is True
    assert _eval_js("isTreeContextMenuKey({ key: 'F10', shiftKey: true })") is True
    assert _eval_js("isTreeContextMenuKey({ key: 'F10', shiftKey: false })") is False
    assert _eval_js("isTreeContextMenuKey({ key: 'Enter', shiftKey: false })") is False
    assert _eval_js("isTreeContextMenuKey({ key: ' ', shiftKey: false })") is False


@pytest.mark.unit
def test_menu_coords_anchor_to_the_focused_row():
    """Keyboard-opened menus have no mouse point; pin them to the row box."""
    assert _eval_js("menuCoordsFromRect({ left: 40, right: 180, top: 10, bottom: 34 })") == {
        "x": 40,
        "y": 34,
    }
