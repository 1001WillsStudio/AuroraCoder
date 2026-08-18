"""Regression: Workspace tree must list files nested deeper than five folders.

Explorer (fresh page load): upload a project containing
``sample-project/a/b/c/d/e/f/deep.txt`` (six directories under the project
root). Expanding sample-project → a → b → c → d showed an empty folder.
``e``, ``f``, and ``deep.txt`` never appeared, even though GET
``/api/files/read`` and Export as .zip both included the file.

Cause: ``build_file_tree`` and the Workspace client both defaulted to
``max_depth=5``, so folder ``d`` was listed with empty children. The
server-side tree cache also ignored ``max_depth``, so a later request with
``max_depth=20`` reused the truncated snapshot.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from gateway import workspace as ws

ROOT = Path(__file__).resolve().parent.parent
TREE_JSX = ROOT / "frontend" / "src" / "components" / "FileTree.jsx"

# Explorer path: six dirs under the uploaded project, eight components from
# the workspace root. Listed when walking ``f`` at current_depth=7.
DEEP_REL = "sample-project/a/b/c/d/e/f/deep.txt"


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


@pytest.fixture
def reset_tree_cache():
    """Module-level cache must not leak between these tests."""
    ws._tree_cache.update({
        "tree": [],
        "root": "",
        "timestamp": 0.0,
        "version": 0,
        "max_depth": None,
    })
    ws._files_changed = True
    yield
    ws._files_changed = True


@pytest.mark.unit
def test_default_tree_includes_file_six_folders_under_project(tmp_workspace):
    """The reported upload: expand d and deep.txt must be in the tree."""
    _plant_deep_project(tmp_workspace)
    tree = ws.build_file_tree(tmp_workspace, tmp_workspace)
    node = _find_node(tree, DEEP_REL)
    assert node is not None, f"tree omitted {DEEP_REL}: {tree!r}"
    assert node["type"] == "file"
    assert node["name"] == "deep.txt"
    folder_d = _find_node(tree, "sample-project/a/b/c/d")
    assert folder_d is not None
    assert any(child["name"] == "e" for child in folder_d.get("children") or [])


@pytest.mark.unit
def test_depth_five_still_truncates_at_folder_d(tmp_workspace):
    """Documents the old cap so a future default change cannot hide it."""
    _plant_deep_project(tmp_workspace)
    tree = ws.build_file_tree(tmp_workspace, tmp_workspace, max_depth=5)
    assert _find_node(tree, DEEP_REL) is None
    folder_d = _find_node(tree, "sample-project/a/b/c/d")
    assert folder_d is not None
    assert folder_d.get("children") == []


@pytest.mark.unit
def test_tree_cache_does_not_reuse_shallower_snapshot(tmp_workspace, reset_tree_cache, monkeypatch):
    """A cached max_depth=5 tree must not be returned for max_depth=20."""
    _plant_deep_project(tmp_workspace)
    monkeypatch.setattr(ws.time, "time", lambda: 1_000.0)

    shallow, _, _ = ws.get_cached_file_tree(tmp_workspace, tmp_workspace, max_depth=5)
    assert _find_node(shallow, DEEP_REL) is None

    deeper, _, _ = ws.get_cached_file_tree(tmp_workspace, tmp_workspace, max_depth=20)
    node = _find_node(deeper, DEEP_REL)
    assert node is not None, "cached depth-5 tree was reused for a deeper request"


@pytest.mark.unit
def test_file_tree_client_requests_depth_beyond_five():
    """Workspace UI must ask for enough depth to show the explorer path."""
    src = TREE_JSX.read_text(encoding="utf-8")
    assert "max_depth=5" not in src
    const_match = re.search(r"FILE_TREE_MAX_DEPTH\s*=\s*(\d+)", src)
    query_match = re.search(r"/api/files/tree\?max_depth=(\d+)", src)
    depth = None
    if const_match:
        depth = int(const_match.group(1))
        assert "max_depth=${FILE_TREE_MAX_DEPTH}" in src or (
            query_match and int(query_match.group(1)) == depth
        )
    elif query_match:
        depth = int(query_match.group(1))
    assert depth is not None, "FileTree must request /api/files/tree with max_depth"
    # eight path components from workspace root to deep.txt; need depth > 7
    assert depth >= 8
    assert depth == ws.FILE_TREE_MAX_DEPTH
