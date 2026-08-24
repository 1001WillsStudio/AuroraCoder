"""Attach workspace files to a chat turn — the file-tree ↔ agent loop.

Hypothesis: users can open files in the viewer but cannot point the agent
at them. These tests are the smallest experiment: wrap/strip for the
model, path validation, and a depth-uncapped workspace search that the
tree itself cannot do.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from gateway.attached_files import (
    ATTACHED_FILES_START,
    MAX_ATTACHED_FILES,
    apply_attached_files,
    extract_attached_files,
    normalize_attached_paths,
    prepare_attached_message,
    strip_attached_files,
)
from gateway.conversation_store import _extract_title
from gateway.task_instruction_display import (
    sanitize_frontend_messages,
    user_message_for_frontend,
)
from gateway.workspace import search_workspace_files


INSTRUCTION = "ALWAYS reply in ALL CAPS."
WRAPPED_TASK = (
    "[TASK INSTRUCTION]\n"
    f"{INSTRUCTION}\n"
    "[/TASK INSTRUCTION]\n\n"
    "Ping"
)


def _plant(ws: Path) -> None:
    deep = ws / "sample-project" / "a" / "b" / "c" / "d" / "e" / "f" / "deep.txt"
    deep.parent.mkdir(parents=True)
    deep.write_text("nested\n", encoding="utf-8")
    (ws / "src").mkdir()
    (ws / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
    (ws / "src" / "utils.py").write_text("x = 1\n", encoding="utf-8")
    (ws / "README.md").write_text("# hi\n", encoding="utf-8")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "pkg.js").write_text("skip\n", encoding="utf-8")


# --------------------------------------------------------------------------- wrap / strip / stack
def test_wrap_roundtrip_does_not_leak_markers_into_user_text():
    wrapped = apply_attached_files("fix the bug", ["src/main.py", "src/utils.py"])
    assert ATTACHED_FILES_START in wrapped
    assert "src/main.py" in wrapped
    assert "read_file" in wrapped
    assert strip_attached_files(wrapped) == "fix the bug"
    assert extract_attached_files(wrapped) == ["src/main.py", "src/utils.py"]


def test_wrap_stacks_after_task_instruction():
    wrapped = apply_attached_files(WRAPPED_TASK, ["src/main.py"])
    task_end = wrapped.find("[/TASK INSTRUCTION]")
    files_start = wrapped.find(ATTACHED_FILES_START)
    ping = wrapped.find("Ping")
    assert 0 <= task_end < files_start < ping
    assert strip_attached_files(wrapped).startswith("[TASK INSTRUCTION]")
    assert extract_attached_files(wrapped) == ["src/main.py"]


def test_apply_replaces_stale_block_instead_of_nesting():
    once = apply_attached_files("hello", ["a.py"])
    twice = apply_attached_files(once, ["b.py"])
    assert twice.count(ATTACHED_FILES_START) == 1
    assert extract_attached_files(twice) == ["b.py"]
    assert strip_attached_files(twice) == "hello"


def test_empty_paths_is_noop():
    assert apply_attached_files("hello", []) == "hello"
    assert extract_attached_files("hello") is None


# --------------------------------------------------------------------------- path validation
def test_normalize_keeps_workspace_files_and_posix_paths(tmp_workspace):
    _plant(tmp_workspace)
    kept = normalize_attached_paths(
        ["src/main.py", "src\\utils.py", "src/main.py"],
        tmp_workspace,
    )
    assert kept == ["src/main.py", "src/utils.py"]


def test_normalize_rejects_traversal_missing_and_directories(tmp_workspace):
    _plant(tmp_workspace)
    (tmp_workspace / "secret.txt").write_text("nope\n", encoding="utf-8")
    kept = normalize_attached_paths(
        [
            "../secret.txt",
            "src/../../secret.txt",
            "src",
            "does-not-exist.py",
            "sample-project/a/b/c/d/e/f/deep.txt",
        ],
        tmp_workspace,
    )
    assert kept == ["sample-project/a/b/c/d/e/f/deep.txt"]


def test_normalize_rejects_absolute_paths_outside_workspace(tmp_workspace):
    _plant(tmp_workspace)
    inside = tmp_workspace / "src" / "main.py"
    kept = normalize_attached_paths(
        ["/etc/passwd", str(inside), "../secret.txt"],
        tmp_workspace,
    )
    assert kept == ["src/main.py"]


def test_normalize_caps_attachments(tmp_workspace):
    files = []
    for i in range(MAX_ATTACHED_FILES + 3):
        p = tmp_workspace / f"f{i}.txt"
        p.write_text("x\n", encoding="utf-8")
        files.append(p.name)
    kept = normalize_attached_paths(files, tmp_workspace)
    assert len(kept) == MAX_ATTACHED_FILES
    assert kept[0] == "f0.txt"


def test_normalize_without_workspace_drops_everything():
    assert normalize_attached_paths(["a.py"], None) == []


def test_prepare_prefers_structured_list_and_rewrites_block(tmp_workspace):
    _plant(tmp_workspace)
    stale = apply_attached_files("please review", ["README.md"])
    message, kept = prepare_attached_message(
        stale, ["src/main.py"], tmp_workspace,
    )
    assert kept == ["src/main.py"]
    assert extract_attached_files(message) == ["src/main.py"]
    assert "README.md" not in message
    assert strip_attached_files(message) == "please review"


# --------------------------------------------------------------------------- search (tree cannot see depth-6 files)
def test_search_finds_files_deeper_than_the_tree_cap(tmp_workspace):
    _plant(tmp_workspace)
    hits = search_workspace_files(tmp_workspace, "deep")
    paths = [h["path"] for h in hits]
    assert "sample-project/a/b/c/d/e/f/deep.txt" in paths
    assert all("node_modules" not in p for p in paths)


def test_search_ranks_filename_hits_ahead_of_path_hits(tmp_workspace):
    _plant(tmp_workspace)
    (tmp_workspace / "main-notes.md").write_text("x\n", encoding="utf-8")
    hits = search_workspace_files(tmp_workspace, "main")
    paths = [h["path"] for h in hits]
    assert paths[0] == "src/main.py"


def test_search_empty_query_returns_nothing(tmp_workspace):
    _plant(tmp_workspace)
    assert search_workspace_files(tmp_workspace, "  ") == []


# --------------------------------------------------------------------------- transcript sanitizer / titles
def test_user_bubble_strips_attached_files_into_chip_field():
    wrapped = apply_attached_files("Ping", ["src/main.py"])
    out = user_message_for_frontend(wrapped)
    assert out["content"] == "Ping"
    assert ATTACHED_FILES_START not in out["content"]
    assert out["attachedFiles"] == ["src/main.py"]


def test_sanitize_keeps_task_chip_and_file_chips_together():
    wrapped = apply_attached_files(WRAPPED_TASK, ["src/main.py"])
    out = sanitize_frontend_messages([{"role": "user", "content": wrapped}])
    assert out[0]["content"] == "Ping"
    assert out[0]["taskInstruction"] == INSTRUCTION
    assert out[0]["attachedFiles"] == ["src/main.py"]
    assert ATTACHED_FILES_START not in out[0]["content"]
    assert "TASK INSTRUCTION" not in out[0]["content"]


def test_title_uses_user_text_not_attached_paths():
    wrapped = apply_attached_files("Please fix the flaky test", ["src/main.py"])
    title = _extract_title([{"role": "user", "content": wrapped}])
    assert title == "Please fix the flaky test"
    assert "main.py" not in title
    assert ATTACHED_FILES_START not in title


def test_sanitize_preserves_existing_attached_files_field():
    wrapped = apply_attached_files("Ping", ["src/main.py"])
    out = sanitize_frontend_messages([{
        "role": "user",
        "content": wrapped,
        "attachedFiles": ["already.py"],
    }])
    assert out[0]["attachedFiles"] == ["already.py"]
    assert out[0]["content"] == "Ping"


def test_title_falls_back_to_filename_when_message_is_only_attachments():
    wrapped = apply_attached_files("", ["src/main.py"])
    title = _extract_title([{"role": "user", "content": wrapped}])
    assert title == "src/main.py"
