"""Regression: opening a depth-capped folder lists one more level on demand.

Explorer: ``sample-project/a/b/c/d/e/f/deep.txt`` never appeared because
the first listing stops at ``max_depth=5`` (folder ``d`` has empty
children). The first-paint tree is unchanged. Clicking ``d`` scans only
that folder one level (``e``); clicking ``e`` then ``f`` reaches
``deep.txt``. Closing the folder drops the on-demand children.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gateway import workspace as ws

ROOT = Path(__file__).resolve().parent.parent
TREE_JSX = ROOT / "frontend" / "src" / "components" / "FileTree.jsx"
TREE_UTIL = ROOT / "frontend" / "src" / "utils" / "fileTree.js"
DEEP_REL = "sample-project/a/b/c/d/e/f/deep.txt"
FOLDER_D = "sample-project/a/b/c/d"
FOLDER_E = "sample-project/a/b/c/d/e"
FOLDER_F = "sample-project/a/b/c/d/e/f"


def _plant_deep_project(root: Path) -> None:
    deep = root / Path(DEEP_REL)
    deep.parent.mkdir(parents=True)
    deep.write_text("deep file\n", encoding="utf-8")
    (root / "sample-project" / "src").mkdir()
    (root / "sample-project" / "config.json").write_text("{}\n", encoding="utf-8")


def _eval_js(expr: str):
    script = (
        "import { expandedEmptyFolderPaths, setFolderChildren } from "
        f"{json.dumps(TREE_UTIL.resolve().as_uri())}\n"
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


def _find_node(tree: list, path: str):
    for node in tree:
        if node.get("path") == path:
            return node
        found = _find_node(node.get("children") or [], path)
        if found is not None:
            return found
    return None


@pytest.fixture
def reset_tree_cache():
    ws._tree_cache.update({
        "tree": [],
        "root": "",
        "timestamp": 0.0,
        "version": 0,
    })
    ws._files_changed = True
    yield
    ws._files_changed = True


@pytest.mark.unit
def test_first_paint_still_stops_at_folder_d(tmp_workspace):
    """The original depth-5 listing is unchanged."""
    _plant_deep_project(tmp_workspace)
    tree = ws.build_file_tree(tmp_workspace, tmp_workspace)
    assert _find_node(tree, DEEP_REL) is None
    folder_d = _find_node(tree, FOLDER_D)
    assert folder_d is not None
    assert folder_d.get("children") == []


@pytest.mark.unit
def test_one_level_scan_of_d_lists_e_only(tmp_workspace):
    """Clicking d scans one sub-level: e, not f or deep.txt."""
    _plant_deep_project(tmp_workspace)
    opened = ws.list_dir_level(tmp_workspace / FOLDER_D, tmp_workspace)
    names = [n["name"] for n in opened]
    assert names == ["e"]
    assert opened[0]["children"] == []
    assert _find_node(opened, DEEP_REL) is None


@pytest.mark.unit
def test_one_level_scan_reaches_deep_txt_from_f(tmp_workspace):
    """Clicking down e then f lists deep.txt (fully on demand)."""
    _plant_deep_project(tmp_workspace)
    e_kids = ws.list_dir_level(tmp_workspace / FOLDER_E, tmp_workspace)
    assert [n["name"] for n in e_kids] == ["f"]
    f_kids = ws.list_dir_level(tmp_workspace / FOLDER_F, tmp_workspace)
    node = _find_node(f_kids, DEEP_REL)
    assert node is not None
    assert node["name"] == "deep.txt"


@pytest.mark.unit
def test_one_level_scan_does_not_write_the_tree_cache(tmp_workspace, reset_tree_cache, monkeypatch):
    """On-demand listing must not replace the cached first-paint tree."""
    _plant_deep_project(tmp_workspace)
    monkeypatch.setattr(ws.time, "time", lambda: 1_000.0)
    cached, _, _ = ws.get_cached_file_tree(tmp_workspace, tmp_workspace)
    assert _find_node(cached, FOLDER_D) is not None
    ws.list_dir_level(tmp_workspace / FOLDER_D, tmp_workspace)
    again, _, _ = ws.get_cached_file_tree(tmp_workspace, tmp_workspace)
    assert again is cached


@pytest.mark.unit
def test_refetch_restores_expanded_empty_folders_shallowest_first():
    """After Refresh, expanded d/e/f must be reopened parent-first (d then e then f)."""
    snapshot = [{
        "name": "d",
        "path": FOLDER_D,
        "type": "folder",
        "children": [],
    }]
    expanded = [FOLDER_D, FOLDER_E, FOLDER_F]
    assert _eval_js(
        f"expandedEmptyFolderPaths({json.dumps(snapshot)}, {json.dumps(expanded)})"
    ) == [FOLDER_D]

    with_e = _eval_js(
        f"setFolderChildren({json.dumps(snapshot)}, {json.dumps(FOLDER_D)}, "
        f"{json.dumps([{'name': 'e', 'path': FOLDER_E, 'type': 'folder', 'children': []}])})"
    )
    assert _eval_js(
        f"expandedEmptyFolderPaths({json.dumps(with_e)}, {json.dumps(expanded)})"
    ) == [FOLDER_E]

    with_f = _eval_js(
        f"setFolderChildren({json.dumps(with_e)}, {json.dumps(FOLDER_E)}, "
        f"{json.dumps([{'name': 'f', 'path': FOLDER_F, 'type': 'folder', 'children': []}])})"
    )
    assert _eval_js(
        f"expandedEmptyFolderPaths({json.dumps(with_f)}, {json.dumps(expanded)})"
    ) == [FOLDER_F]


@pytest.mark.unit
def test_closed_folder_is_not_restored_after_refetch():
    snapshot = [{
        "name": "d",
        "path": FOLDER_D,
        "type": "folder",
        "children": [],
    }]
    assert _eval_js(
        f"expandedEmptyFolderPaths({json.dumps(snapshot)}, [])"
    ) == []


@pytest.mark.unit
def test_file_tree_client_fetches_one_level_on_click_and_drops_on_close():
    src = TREE_JSX.read_text(encoding="utf-8")
    assert "max_depth=5" in src
    assert "/api/files/tree?path=" in src
    assert "max_depth=1" in src
    assert "restoreExpanded" in src
    assert "expandedEmptyFolderPaths" in src
    assert "onDemandRef" in src
    # Closing an on-demand folder clears children; first-paint folders stay.
    assert "onDemandRef.current.has(path)" in src
    assert "setFolderChildren(prev, path, [])" in src
    assert "FILE_TREE_MAX_NODES" not in src
    assert "truncated" not in src
