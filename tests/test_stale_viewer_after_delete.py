"""Regression: deleting an open workspace file must drop its viewer tab.

Explorer (fresh page load): open README.md in the right-hand viewer, delete
it from the Workspace tree, confirm. The tree updates (file gone) but the
viewer kept the old path, the ``view`` badge, and the previous contents.
Refresh in the viewer header did not re-read; GET /api/files/read returned
404 and the open-file handler swallowed non-OK (``if (!resp.ok) return``).
Refresh also only fetched agent diffs, so a view-only tab never reloaded.

There is no JS test runner here, so behaviour is locked two ways:
  * the extracted helpers in ``frontend/src/utils/panelFiles.js`` are
    executed with Node (hermetic: no network, no DOM);
  * a source scan asserts delete notifies the panel and Refresh re-reads
    view-only tabs, dropping them when the file is gone.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "frontend" / "src" / "utils" / "panelFiles.js"
HOOK = ROOT / "frontend" / "src" / "hooks" / "useFileTracking.js"
TREE = ROOT / "frontend" / "src" / "components" / "FileTree.jsx"
SIDEBAR = ROOT / "frontend" / "src" / "components" / "Sidebar.jsx"
APP = ROOT / "frontend" / "src" / "App.jsx"


def _eval_js(expr: str):
    script = (
        "import {\n"
        "  pathIsDeleted,\n"
        "  closePanelFilesForDeletedPath,\n"
        "  applyViewOnlyReadResults,\n"
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


def _readme_tab(**extra):
    tab = {
        "id": "sample-project/README.md",
        "path": "sample-project/README.md",
        "isViewOnly": True,
        "lines": [
            {"lineNumber": 1, "content": "# Sample", "type": None},
            {"lineNumber": 2, "content": "hello", "type": None},
        ],
    }
    tab.update(extra)
    return tab


@pytest.mark.unit
def test_path_is_deleted_matches_file_and_folder_prefix():
    """Reported path, plus a folder delete, plus a lookalike sibling."""
    assert _eval_js("pathIsDeleted('sample-project/README.md', 'sample-project/README.md')") is True
    assert _eval_js("pathIsDeleted('sample-project/README.md', 'sample-project')") is True
    assert _eval_js("pathIsDeleted('sample-project-2/README.md', 'sample-project')") is False
    assert _eval_js("pathIsDeleted('other.md', 'sample-project/README.md')") is False


@pytest.mark.unit
def test_delete_closes_the_open_viewer_tab():
    """The explorer case: open README.md, delete it, tab must go away."""
    files = json.dumps([_readme_tab()])
    result = _eval_js(
        f"closePanelFilesForDeletedPath({files}, 'sample-project/README.md', 'sample-project/README.md')"
    )
    assert result["files"] == []
    assert result["nextActiveId"] is None


@pytest.mark.unit
def test_delete_folder_closes_tabs_under_it_and_keeps_siblings():
    files = json.dumps([
        _readme_tab(),
        {"id": "other.md", "path": "other.md", "isViewOnly": True, "lines": []},
    ])
    result = _eval_js(
        f"closePanelFilesForDeletedPath({files}, 'sample-project/README.md', 'sample-project')"
    )
    assert [f["id"] for f in result["files"]] == ["other.md"]
    assert result["nextActiveId"] == "other.md"


@pytest.mark.unit
def test_refresh_404_drops_stale_view_only_tab():
    """Refresh must not keep presenting deleted contents after a 404 read."""
    files = json.dumps([
        _readme_tab(),
        {"id": "kept.py", "path": "kept.py", "isViewOnly": True, "lines": []},
    ])
    results = json.dumps([
        {"id": "sample-project/README.md", "missing": True},
        {"id": "kept.py", "content": "print(1)"},
    ])
    next_files = _eval_js(f"applyViewOnlyReadResults({files}, {results})")
    assert [f["id"] for f in next_files] == ["kept.py"]
    assert next_files[0]["lines"] == [
        {"lineNumber": 1, "content": "print(1)", "type": None},
    ]


@pytest.mark.unit
def test_refresh_updates_view_only_contents_when_file_still_exists():
    files = json.dumps([_readme_tab()])
    results = json.dumps([
        {"id": "sample-project/README.md", "content": "new line 1\nnew line 2"},
    ])
    next_files = _eval_js(f"applyViewOnlyReadResults({files}, {results})")
    assert len(next_files) == 1
    assert [line["content"] for line in next_files[0]["lines"]] == ["new line 1", "new line 2"]


@pytest.mark.unit
def test_refresh_does_not_clobber_diff_tabs():
    files = json.dumps([
        {
            "id": "edited.py",
            "path": "edited.py",
            "isViewOnly": False,
            "lines": [{"lineNumber": 1, "content": "diff", "type": "added"}],
        },
    ])
    results = json.dumps([{"id": "edited.py", "missing": True}])
    next_files = _eval_js(f"applyViewOnlyReadResults({files}, {results})")
    assert len(next_files) == 1
    assert next_files[0]["lines"][0]["content"] == "diff"


@pytest.mark.unit
def test_file_tree_notifies_panel_after_successful_delete():
    src = TREE.read_text(encoding="utf-8")
    start = src.index("const handleDeleteConfirm")
    confirm = src[start : start + 900]
    # Successful delete (res.ok) must tell the viewer; failed delete must not.
    assert "onPathDeleted" in confirm
    assert "onPathDeleted?.(confirmDelete.path)" in confirm or "onPathDeleted(confirmDelete.path)" in confirm
    fail_arm = confirm[confirm.index("if (!res.ok)") : confirm.index("} else")]
    assert "onPathDeleted" not in fail_arm


@pytest.mark.unit
def test_refresh_rereads_view_only_tabs_and_drops_missing():
    src = HOOK.read_text(encoding="utf-8")
    assert "from '../utils/panelFiles'" in src
    assert "closePanelFilesForDeletedPath" in src
    assert "applyViewOnlyReadResults" in src
    assert "handlePathDeleted" in src

    refresh = src[src.index("const handleRefreshFiles") : src.index("const handleFileTreeClick")]
    assert "/api/files/read" in refresh
    assert "isViewOnly" in refresh
    assert "if (!resp.ok) return { id: f.id, missing: true }" in refresh
    assert "applyViewOnlyReadResults" in refresh
    # Must not swallow a failed re-read the way open-file does.
    assert "if (!resp.ok) return\n" not in refresh.replace(" ", "")


@pytest.mark.unit
def test_app_and_sidebar_wire_delete_to_the_viewer():
    app = APP.read_text(encoding="utf-8")
    sidebar = SIDEBAR.read_text(encoding="utf-8")
    assert "handlePathDeleted" in app
    assert "onPathDeleted={handlePathDeleted}" in app
    assert "onPathDeleted" in sidebar
    assert "onPathDeleted={onPathDeleted}" in sidebar
