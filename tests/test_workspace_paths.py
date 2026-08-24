"""Shared workspace path containment — HTTP file APIs and attachments.

Hypothesis: five gateway file endpoints copied ``str.startswith`` on
resolved paths. That treats a sibling named ``ws-leaked`` as inside
workspace ``ws``, and ``except Exception`` around ``HTTPException(403)``
turns the intended 403 into 400 (Starlette's HTTPException subclasses
Exception). Chat attachments already used ``is_relative_to``. One helper
should own the check for both surfaces.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from gateway.attached_files import normalize_attached_paths
from gateway.paths import WorkspacePathError, resolve_under_workspace

ROOT = Path(__file__).resolve().parent.parent
ROUTES = ROOT / "gateway" / "routes.py"
ATTACHED = ROOT / "gateway" / "attached_files.py"


@pytest.fixture
def pair(tmp_path: Path):
    """Workspace ``ws`` next to a prefix-sibling ``ws-leaked``."""
    ws = tmp_path / "ws"
    leaked = tmp_path / "ws-leaked"
    ws.mkdir()
    leaked.mkdir()
    (ws / "ok.txt").write_text("workspace-ok\n", encoding="utf-8")
    (ws / "sub").mkdir()
    (ws / "sub" / "nested.txt").write_text("nested\n", encoding="utf-8")
    (leaked / "secret.txt").write_text("LEAKED\n", encoding="utf-8")
    return ws, leaked


def _escape(leaked: Path) -> str:
    return f"../{leaked.name}/secret.txt"


# --------------------------------------------------------------------------- helper
@pytest.mark.unit
def test_resolve_keeps_in_workspace_and_internal_dotdot(pair):
    ws, _leaked = pair
    assert resolve_under_workspace(ws, "ok.txt") == (ws / "ok.txt").resolve()
    assert resolve_under_workspace(ws, "sub/../ok.txt") == (ws / "ok.txt").resolve()
    assert resolve_under_workspace(ws, str(ws / "ok.txt")) == (ws / "ok.txt").resolve()


@pytest.mark.unit
def test_resolve_rejects_prefix_sibling_that_startswith_allows(pair):
    """The bug: ``/tmp/ws-leaked/secret``.startswith(``/tmp/ws``) is True."""
    ws, leaked = pair
    escape = _escape(leaked)
    leaked_secret = (leaked / "secret.txt").resolve()
    naive = (ws / escape).resolve()
    assert naive == leaked_secret
    assert str(naive).startswith(str(ws.resolve()))
    assert not naive.is_relative_to(ws.resolve())

    with pytest.raises(WorkspacePathError) as exc:
        resolve_under_workspace(ws, escape)
    assert exc.value.outside is True

    with pytest.raises(WorkspacePathError) as exc:
        resolve_under_workspace(ws, str(leaked / "secret.txt"))
    assert exc.value.outside is True


@pytest.mark.unit
def test_resolve_rejects_parent_and_symlink_escape(pair):
    ws, leaked = pair
    with pytest.raises(WorkspacePathError) as exc:
        resolve_under_workspace(ws, "..")
    assert exc.value.outside is True

    (ws / "escape_link").symlink_to(leaked / "secret.txt")
    with pytest.raises(WorkspacePathError) as exc:
        resolve_under_workspace(ws, "escape_link")
    assert exc.value.outside is True


@pytest.mark.unit
def test_attachments_drop_prefix_sibling(pair):
    ws, leaked = pair
    kept = normalize_attached_paths(
        ["ok.txt", _escape(leaked), str(leaked / "secret.txt")],
        ws,
    )
    assert kept == ["ok.txt"]


# --------------------------------------------------------------------------- HTTP
@pytest.fixture
def file_client(pair, monkeypatch):
    ws, leaked = pair
    monkeypatch.setattr("gateway.routes._get_workspace", lambda: ws)
    from fastapi.testclient import TestClient
    from gateway.api import app

    return TestClient(app), ws, leaked


@pytest.mark.unit
def test_file_read_allows_workspace_and_internal_dotdot(file_client):
    client, _ws, _leaked = file_client
    ok = client.get("/api/files/read", params={"file_path": "ok.txt"})
    assert ok.status_code == 200
    assert ok.json()["content"] == "workspace-ok\n"

    nested = client.get("/api/files/read", params={"file_path": "sub/../ok.txt"})
    assert nested.status_code == 200
    assert nested.json()["content"] == "workspace-ok\n"


@pytest.mark.unit
def test_file_read_prefix_sibling_is_403_not_400(file_client):
    """Old startswith + except Exception returned 400 Invalid path."""
    client, _ws, leaked = file_client
    response = client.get(
        "/api/files/read", params={"file_path": _escape(leaked)},
    )
    assert response.status_code == 403
    assert "outside" in response.json()["detail"]
    assert "LEAKED" not in response.text


@pytest.mark.unit
def test_file_tree_delete_download_export_reject_prefix_sibling(file_client):
    client, ws, leaked = file_client
    folder = f"../{leaked.name}"
    secret = _escape(leaked)

    tree = client.get("/api/files/tree", params={"path": folder})
    assert tree.status_code == 403, tree.text

    download = client.get("/api/files/download", params={"file_path": secret})
    assert download.status_code == 403

    export = client.get("/api/files/export", params={"folder_path": folder})
    assert export.status_code == 403

    deleted = client.post("/api/files/delete", json={"path": secret})
    assert deleted.status_code == 403
    assert (leaked / "secret.txt").read_text(encoding="utf-8") == "LEAKED\n"
    assert (ws / "ok.txt").exists()


@pytest.mark.unit
def test_missing_in_workspace_file_is_404_not_403(file_client):
    client, _ws, _leaked = file_client
    response = client.get("/api/files/read", params={"file_path": "nope.txt"})
    assert response.status_code == 404


@pytest.mark.unit
def test_tree_lists_in_workspace_folder(file_client):
    client, _ws, _leaked = file_client
    response = client.get("/api/files/tree", params={"path": "sub"})
    assert response.status_code == 200
    names = [n["name"] for n in response.json()["tree"]]
    assert "nested.txt" in names


# --------------------------------------------------------------------------- wiring lock
@pytest.mark.unit
def test_routes_and_attachments_use_the_shared_helper():
    routes = ROUTES.read_text(encoding="utf-8")
    attached = ATTACHED.read_text(encoding="utf-8")
    assert "startswith(str(work_dir" not in routes
    assert "resolve_under_workspace" in routes
    assert routes.count("_require_workspace_path(") == 6  # def + 5 call sites
    assert "from gateway.paths import" in attached
    assert "resolve_under_workspace" in attached
