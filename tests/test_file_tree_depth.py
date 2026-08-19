"""Regression: Workspace tree must list files nested deeper than five folders.

Explorer (fresh page load): upload a project containing
``sample-project/a/b/c/d/e/f/deep.txt`` (six directories under the project
root). Expanding sample-project → a → b → c → d showed an empty folder.
``e``, ``f``, and ``deep.txt`` never appeared, even though GET
``/api/files/read`` and Export as .zip both included the file.

The first listing stays at the historic ``max_depth=5`` (latency guard).
Opening a truncated folder walks that folder with the same cap so ``e``
and ``deep.txt`` appear. The server-side cache must not reuse a shallow
root snapshot when listing a deeper path.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from gateway import workspace as ws

ROOT = Path(__file__).resolve().parent.parent
TREE_JSX = ROOT / "frontend" / "src" / "components" / "FileTree.jsx"
TREE_UTIL = ROOT / "frontend" / "src" / "utils" / "fileTree.js"

# Explorer path: six dirs under the uploaded project, eight components from
# the workspace root. Listed when walking folder ``d`` (not the workspace root).
DEEP_REL = "sample-project/a/b/c/d/e/f/deep.txt"
FOLDER_D = "sample-project/a/b/c/d"


def _plant_deep_project(root: Path) -> Path:
    deep = root / Path(DEEP_REL)
    deep.parent.mkdir(parents=True)
    deep.write_text("deep file\n", encoding="utf-8")
    (root / "sample-project" / "src").mkdir()
    (root / "sample-project" / "config.json").write_text("{}\n", encoding="utf-8")
    return deep


def _find_node(tree: list, path: str):
    for node in tree:
        if node.get("path") == path:
            return node
        found = _find_node(node.get("children") or [], path)
        if found is not None:
            return found
    return None


def _count_nodes(tree: list) -> int:
    return sum(1 + _count_nodes(node.get("children") or []) for node in tree)


def _eval_js(expr: str):
    script = (
        "import { findTreeNode, mergeFolderChildren } from "
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


@pytest.fixture
def reset_tree_cache():
    """Module-level cache must not leak between these tests."""
    ws._tree_cache.update({
        "tree": [],
        "root": "",
        "timestamp": 0.0,
        "version": 0,
        "max_depth": None,
        "max_nodes": None,
        "truncated": False,
    })
    ws._files_changed = True
    yield
    ws._files_changed = True


@pytest.mark.unit
def test_first_paint_keeps_historic_depth_five(tmp_workspace):
    """Starting stage: same cap as today. Folder d is listed but not walked."""
    _plant_deep_project(tmp_workspace)
    assert ws.FILE_TREE_MAX_DEPTH == 5
    tree = ws.build_file_tree(tmp_workspace, tmp_workspace)
    assert _find_node(tree, DEEP_REL) is None
    folder_d = _find_node(tree, FOLDER_D)
    assert folder_d is not None
    assert folder_d.get("children") == []
    assert folder_d.get("truncated") is True


@pytest.mark.unit
def test_opening_truncated_folder_lists_deep_file(tmp_workspace):
    """User opens d: walk that folder with the same depth cap → deep.txt."""
    _plant_deep_project(tmp_workspace)
    opened = ws.build_file_tree(tmp_workspace / FOLDER_D, tmp_workspace)
    node = _find_node(opened, DEEP_REL)
    assert node is not None, f"opening {FOLDER_D} omitted {DEEP_REL}: {opened!r}"
    assert node["type"] == "file"
    assert node["name"] == "deep.txt"
    assert any(child["name"] == "e" for child in opened)


@pytest.mark.unit
def test_tree_cache_does_not_reuse_root_snapshot_for_opened_folder(
    tmp_workspace, reset_tree_cache, monkeypatch
):
    """A cached workspace listing must not be returned for folder d."""
    _plant_deep_project(tmp_workspace)
    monkeypatch.setattr(ws.time, "time", lambda: 1_000.0)

    shallow, _, _, _ = ws.get_cached_file_tree(tmp_workspace, tmp_workspace)
    assert _find_node(shallow, DEEP_REL) is None

    opened, _, _, _ = ws.get_cached_file_tree(
        tmp_workspace / FOLDER_D, tmp_workspace
    )
    node = _find_node(opened, DEEP_REL)
    assert node is not None, "cached root tree was reused when opening folder d"


@pytest.mark.unit
def test_file_tree_client_opens_truncated_folder():
    """First fetch stays at depth 5; opening a truncated folder sends path=."""
    src = TREE_JSX.read_text(encoding="utf-8")
    assert re.search(r"FILE_TREE_MAX_DEPTH\s*=\s*5\b", src)
    assert "/api/files/tree?max_depth=${FILE_TREE_MAX_DEPTH}" in src
    assert "/api/files/tree?path=" in src
    assert "mergeFolderChildren" in src
    assert "node.truncated" in src
    assert "from '../utils/fileTree'" in src
    assert ws.FILE_TREE_MAX_DEPTH == 5
    assert ws.FILE_TREE_MAX_NODES < 10_000


@pytest.mark.unit
def test_merge_folder_children_replaces_truncated_node():
    """Opening d must splice e/f/deep.txt onto the existing snapshot."""
    tree = [{
        "name": "d",
        "path": FOLDER_D,
        "type": "folder",
        "children": [],
        "truncated": True,
    }]
    children = [{
        "name": "e",
        "path": f"{FOLDER_D}/e",
        "type": "folder",
        "children": [{
            "name": "deep.txt",
            "path": DEEP_REL,
            "type": "file",
        }],
    }]
    merged = _eval_js(
        f"mergeFolderChildren({json.dumps(tree)}, {json.dumps(FOLDER_D)}, "
        f"{json.dumps(children)}, false)"
    )
    node = _eval_js(f"findTreeNode({json.dumps(merged)}, {json.dumps(DEEP_REL)})")
    assert node is not None
    assert node["name"] == "deep.txt"
    d = _eval_js(f"findTreeNode({json.dumps(merged)}, {json.dumps(FOLDER_D)})")
    assert "truncated" not in d


@pytest.mark.unit
def test_node_budget_caps_a_wide_tree(tmp_workspace):
    """A large flat folder must not be walked in full — that is the width guard."""
    wide = tmp_workspace / "wide"
    wide.mkdir()
    for i in range(50):
        (wide / f"f{i:02d}.txt").write_text("x", encoding="utf-8")
    tree = ws.build_file_tree(tmp_workspace, tmp_workspace, max_nodes=10)
    assert _count_nodes(tree) <= 10
    folder = _find_node(tree, "wide")
    assert folder is not None
    assert folder.get("truncated") is True
    assert len(folder.get("children") or []) < 50


@pytest.mark.unit
def test_node_budget_still_lists_the_reported_deep_file(tmp_workspace):
    """Opening d is a handful of nodes; the budget must not hide deep.txt."""
    _plant_deep_project(tmp_workspace)
    tree = ws.build_file_tree(tmp_workspace / FOLDER_D, tmp_workspace, max_nodes=20)
    assert _find_node(tree, DEEP_REL) is not None


@pytest.mark.unit
def test_directory_symlink_is_not_walked(tmp_workspace):
    """A symlink to a large tree must not be followed during the walk."""
    real = tmp_workspace / "real"
    real.mkdir()
    (real / "secret.txt").write_text("x", encoding="utf-8")
    link = tmp_workspace / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not supported here")
    tree = ws.build_file_tree(tmp_workspace, tmp_workspace)
    linked = _find_node(tree, "link")
    assert linked is not None
    assert linked.get("children") == []
    assert _find_node(tree, "link/secret.txt") is None
