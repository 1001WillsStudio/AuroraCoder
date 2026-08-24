"""Composer helpers and UI contracts for attaching workspace files to chat."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "frontend" / "src" / "utils" / "attachedFiles.js"
CHAT_INPUT = ROOT / "frontend" / "src" / "components" / "ChatInput.jsx"
FILE_TREE = ROOT / "frontend" / "src" / "components" / "FileTree.jsx"
APP = ROOT / "frontend" / "src" / "App.jsx"
ROUTES = ROOT / "gateway" / "routes.py"
TRANSLATIONS = ROOT / "frontend" / "src" / "i18n" / "translations.js"


def _eval_js(expr: str):
    script = (
        "import {\n"
        "  addAttachedFile, removeAttachedFile, parseAtQuery, applyAtPick,\n"
        "  fileNameOf, MAX_ATTACHED_FILES, enterActionForPicker,\n"
        "  shouldApplySearchResults,\n"
        "} from "
        f"{json.dumps(HELPER.resolve().as_uri())}\n"
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
def test_parse_at_query_requires_boundary():
    assert _eval_js("parseAtQuery('look at @main', 13)") == {
        "query": "main", "start": 8, "end": 13,
    }
    assert _eval_js("parseAtQuery('user@host', 9)") is None
    assert _eval_js("parseAtQuery('@', 1)") == {"query": "", "start": 0, "end": 1}


@pytest.mark.unit
def test_apply_at_pick_removes_token_without_inserting_path():
    assert _eval_js("applyAtPick('fix @main please', {start: 4, end: 9})") == "fix please"


@pytest.mark.unit
def test_add_attached_file_is_unique_and_capped():
    out = _eval_js(
        """(() => {
          let files = [];
          files = addAttachedFile(files, 'src/a.py');
          files = addAttachedFile(files, 'src/a.py');
          files = addAttachedFile(files, 'src\\\\b.py');
          let capped = [];
          for (let i = 0; i < MAX_ATTACHED_FILES + 3; i++) {
            capped = addAttachedFile(capped, 'f' + i + '.txt');
          }
          return {
            files,
            name: fileNameOf('src/deep/main.py'),
            cappedLen: capped.length,
            max: MAX_ATTACHED_FILES,
            removed: removeAttachedFile(['a.py', 'b.py'], 'a.py'),
          };
        })()"""
    )
    assert out["files"] == ["src/a.py", "src/b.py"]
    assert out["name"] == "main.py"
    assert out["cappedLen"] == out["max"]
    assert out["removed"] == ["b.py"]


@pytest.mark.unit
def test_composer_and_tree_expose_attach_controls():
    chat = CHAT_INPUT.read_text(encoding="utf-8")
    tree = FILE_TREE.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    routes = ROUTES.read_text(encoding="utf-8")
    assert 'data-testid="chat-attach"' in chat
    assert "parseAtQuery" in chat
    assert "fileTree.addToChat" in tree
    assert 'data-testid="file-tree-add-to-chat"' in tree
    assert "attached_files:" in app
    assert "searchWorkspaceFiles" in app
    assert "apply_request_attachments" in routes
    assert "/api/files/search" in routes


@pytest.mark.unit
def test_enter_sends_when_picker_has_no_hits():
    """Empty / no-match @ picker must not swallow Enter."""
    assert _eval_js("enterActionForPicker(true, 0)") == "send"
    assert _eval_js("enterActionForPicker(true, 2)") == "pick"
    assert _eval_js("enterActionForPicker(false, 0)") == "send"
    chat = CHAT_INPUT.read_text(encoding="utf-8")
    assert "enterActionForPicker" in chat
    # The old swallow: pickerOpen + Enter + return without send.
    assert "if (pickerOpen) {" not in chat


@pytest.mark.unit
def test_stale_search_results_are_ignored():
    assert _eval_js("shouldApplySearchResults(1, 1)") is True
    assert _eval_js("shouldApplySearchResults(1, 2)") is False
    assert "shouldApplySearchResults" in CHAT_INPUT.read_text(encoding="utf-8")


@pytest.mark.unit
def test_retry_keeps_attachment_only_turns():
    """Files-only send used to bail in handleRetry because message was ''. """
    src = APP.read_text(encoding="utf-8")
    retry = src.split("const handleRetry =", 1)[1].split("// ── Render", 1)[0]
    assert "retryFiles" in retry
    assert "attached_files: retryFiles" in retry
    assert "if (!message) return" not in retry


@pytest.mark.unit
def test_picker_closes_on_outside_click():
    chat = CHAT_INPUT.read_text(encoding="utf-8")
    assert "addEventListener('mousedown', onPointerDown)" in chat
    assert "setAtQuery(null)" in chat


@pytest.mark.unit
def test_attach_strings_exist_in_both_languages():
    src = TRANSLATIONS.read_text(encoding="utf-8")
    for key in (
        "chat.hint.attach",
        "chat.attachFileTitle",
        "chat.attachedFilesChip",
        "chat.attachFileLimit",
        "fileTree.addToChat",
    ):
        assert src.count(f"'{key}'") >= 2, key
