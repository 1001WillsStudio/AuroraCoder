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
    AttachedFilesError,
    apply_attached_files,
    apply_request_attachments,
    coerce_attached_files,
    extract_attached_files,
    normalize_attached_paths,
    prepare_attached_message,
    strip_attached_files,
)
from gateway.conversation_store import ConversationStore, _extract_title
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


def test_title_uses_attached_files_field_after_sanitize():
    """Reload sees the chip field, not the marked block."""
    title = _extract_title([{
        "role": "user",
        "content": "",
        "attachedFiles": ["src/main.py"],
    }])
    assert title == "src/main.py"


# --------------------------------------------------------------------------- request body / type check / refusal
def test_coerce_rejects_string_so_characters_are_not_paths():
    with pytest.raises(AttachedFilesError, match="list of paths"):
        coerce_attached_files("src/main.py")
    with pytest.raises(AttachedFilesError, match="list of paths"):
        coerce_attached_files(["src/main.py", 1])
    assert coerce_attached_files(None) is None
    assert coerce_attached_files(["src/main.py"]) == ["src/main.py"]


def test_apply_request_refuses_when_every_path_is_unusable(tmp_workspace):
    _plant(tmp_workspace)
    body = {"message": "", "attached_files": ["missing.py", "../secret.txt"]}
    with pytest.raises(AttachedFilesError, match="None of the attached"):
        apply_request_attachments(body, tmp_workspace)
    assert "attached_files" not in body


def test_apply_request_keeps_valid_and_drops_stale(tmp_workspace):
    _plant(tmp_workspace)
    body = {"message": "please review", "attached_files": ["src/main.py", "missing.py"]}
    kept = apply_request_attachments(body, tmp_workspace)
    assert kept == ["src/main.py"]
    assert "attached_files" not in body
    assert extract_attached_files(body["message"]) == ["src/main.py"]
    assert strip_attached_files(body["message"]) == "please review"


def test_apply_request_plain_message_is_untouched(tmp_workspace):
    _plant(tmp_workspace)
    body = {"message": "hello"}
    assert apply_request_attachments(body, tmp_workspace) == []
    assert body["message"] == "hello"


# --------------------------------------------------------------------------- ConversationStore seed / reload
def test_seed_and_reload_keep_attached_file_chips(tmp_path):
    store = ConversationStore(storage_dir=tmp_path)
    cid = store.create_conversation()
    wrapped = apply_attached_files("Please review", ["src/main.py"])
    store.seed_frontend_user_message(cid, wrapped)

    reloaded = store.get_frontend_messages(cid)
    assert reloaded[0]["content"] == "Please review"
    assert reloaded[0]["attachedFiles"] == ["src/main.py"]
    assert ATTACHED_FILES_START not in reloaded[0]["content"]
    assert store.get_conversation(cid)["title"] == "Please review"


def test_seed_files_only_keeps_chips_and_filename_title(tmp_path):
    store = ConversationStore(storage_dir=tmp_path)
    cid = store.create_conversation()
    wrapped = apply_attached_files("", ["src/main.py"])
    store.seed_frontend_user_message(cid, wrapped)

    reloaded = store.get_frontend_messages(cid)
    assert reloaded[0]["content"] == ""
    assert reloaded[0]["attachedFiles"] == ["src/main.py"]
    assert store.get_conversation(cid)["title"] == "src/main.py"


# --------------------------------------------------------------------------- /api/chat (runtime, mocked backend stream)
def _chat_client(tmp_workspace, monkeypatch):
    captured: list = []

    async def fake_proxy(stream, body):
        captured.append(dict(body))
        stream.finished = True

    async def fake_sse(stream, queue, request, replay_latest=False):
        yield "data: {}\n\n"

    monkeypatch.setattr("gateway.routes._get_workspace", lambda: tmp_workspace)
    monkeypatch.setattr("gateway.routes.WORKSPACE", tmp_workspace)
    monkeypatch.setattr("gateway.routes._proxy_backend_stream", fake_proxy)
    monkeypatch.setattr("gateway.routes._subscriber_sse", fake_sse)

    from fastapi.testclient import TestClient
    from gateway.api import app

    return TestClient(app), captured


def test_chat_route_seeds_kept_attachments_and_drops_stale(tmp_workspace, monkeypatch):
    _plant(tmp_workspace)
    client, captured = _chat_client(tmp_workspace, monkeypatch)
    response = client.post("/api/chat", json={
        "message": "please review",
        "attached_files": ["src/main.py", "missing.py"],
    })
    assert response.status_code == 200
    cid = response.headers["x-conversation-id"]
    assert cid
    assert captured, "backend proxy should have been given a rewritten body"
    assert "attached_files" not in captured[0]
    assert extract_attached_files(captured[0]["message"]) == ["src/main.py"]
    assert "missing.py" not in captured[0]["message"]

    reloaded = client.get(f"/api/conversations/{cid}").json()
    bubble = reloaded["frontend_messages"][0]
    assert bubble["content"] == "please review"
    assert bubble["attachedFiles"] == ["src/main.py"]
    assert ATTACHED_FILES_START not in bubble["content"]


def test_chat_route_refuses_files_only_when_all_paths_invalid(tmp_workspace, monkeypatch):
    _plant(tmp_workspace)
    client, captured = _chat_client(tmp_workspace, monkeypatch)
    cid = "attach-refuse-all-invalid"
    response = client.post("/api/chat", json={
        "message": "",
        "conversation_id": cid,
        "attached_files": ["does-not-exist.py"],
    })
    assert response.status_code == 400
    assert "None of the attached" in response.json()["detail"]
    assert captured == []
    assert client.get(f"/api/conversations/{cid}").status_code == 404


def test_chat_route_rejects_non_list_attached_files(tmp_workspace, monkeypatch):
    _plant(tmp_workspace)
    client, captured = _chat_client(tmp_workspace, monkeypatch)
    response = client.post("/api/chat", json={
        "message": "please review",
        "attached_files": "src/main.py",
    })
    assert response.status_code == 400
    assert "list of paths" in response.json()["detail"]
    assert captured == []
