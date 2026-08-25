"""Desktop workspace/discover fetches must send the Bearer token.

Explorer: with ACCESS_PASSWORD set, chat worked after login (api.js adds
Authorization) but the file tree, viewer, download/export, and Discover
Models called /api/files and /api/discover-models with a bare fetch or
``<a href>``. Those routes 401 without a token, so a passworded instance
showed an empty workspace and silent download failures.

Download/export cannot use ``<a href>`` — the browser will not attach
Authorization — so they fetch a blob with the same header helper.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
API_JS = ROOT / "frontend" / "src" / "services" / "api.js"
TREE = ROOT / "frontend" / "src" / "components" / "FileTree.jsx"
HOOK = ROOT / "frontend" / "src" / "hooks" / "useFileTracking.js"
SETTINGS = ROOT / "frontend" / "src" / "components" / "SettingsPanel.jsx"

BARE_FILE_FETCH = re.compile(
    r"""fetch\s*\(\s*(['"`])/api/files"""
)
BARE_DISCOVER_FETCH = re.compile(
    r"""fetch\s*\(\s*(['"`])/api/discover-models"""
)
HREF_DOWNLOAD = re.compile(
    r"""a\.href\s*=\s*(['"`])/api/files/(?:download|export)"""
)


def _eval_file_tree_url(expr: str):
    script = (
        "import { fileTreeUrl } from "
        f"{json.dumps(API_JS.resolve().as_uri())}\n"
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
def test_file_tree_url_keeps_first_paint_and_one_level_query():
    assert _eval_file_tree_url("fileTreeUrl({ maxDepth: 5 })") == (
        "/api/files/tree?max_depth=5"
    )
    assert _eval_file_tree_url(
        "fileTreeUrl({ path: 'sample-project/a/b/c/d', maxDepth: 1 })"
    ) == "/api/files/tree?path=sample-project%2Fa%2Fb%2Fc%2Fd&max_depth=1"


@pytest.mark.unit
def test_workspace_helpers_send_auth_headers():
    api = API_JS.read_text(encoding="utf-8")
    for name in (
        "getFileTree",
        "readWorkspaceFile",
        "getFileDiffs",
        "deleteWorkspacePath",
        "downloadWorkspaceFile",
        "exportWorkspaceFolder",
        "discoverProviderModels",
    ):
        assert f"export async function {name}" in api

    assert "headers: _headers()" in api
    assert "triggerBrowserDownload" in api
    assert "response.blob()" in api
    # Blob download is the only path that can carry Bearer; a raw href cannot.
    assert "a.href = `/api/files/download" not in api
    assert "a.href = `/api/files/export" not in api


@pytest.mark.unit
def test_tree_viewer_and_discover_do_not_bare_fetch():
    tree = TREE.read_text(encoding="utf-8")
    hook = HOOK.read_text(encoding="utf-8")
    settings = SETTINGS.read_text(encoding="utf-8")

    assert "from '../services/api'" in tree
    assert "getFileTree" in tree
    assert "deleteWorkspacePath" in tree
    assert "downloadWorkspaceFile" in tree
    assert "exportWorkspaceFolder" in tree
    assert BARE_FILE_FETCH.search(tree) is None
    assert HREF_DOWNLOAD.search(tree) is None

    assert "readWorkspaceFile" in hook
    assert "getFileDiffs" in hook
    assert BARE_FILE_FETCH.search(hook) is None

    assert "discoverProviderModels" in settings
    assert BARE_DISCOVER_FETCH.search(settings) is None


def _http_scope(path, headers=None):
    raw = []
    for key, value in (headers or {}).items():
        raw.append((key.lower().encode("latin-1"), value.encode("latin-1")))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": raw,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }


@pytest.mark.unit
async def test_passworded_file_apis_require_bearer(monkeypatch):
    """ACCESS_PASSWORD gates /api/files the same as /api/chat (experiment)."""
    monkeypatch.setattr("gateway.api.AUTH_PASSWORD", "secret")
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response

    from gateway.api import _auth_tokens, auth_middleware

    async def ok(_request):
        return Response("ok", status_code=200)

    denied = await auth_middleware(Request(_http_scope("/api/files/tree")), ok)
    assert denied.status_code == 401
    assert isinstance(denied, JSONResponse)
    assert "Bearer" in denied.body.decode()

    _auth_tokens["test-token"] = 1e18
    allowed = await auth_middleware(
        Request(_http_scope("/api/files/tree", {"authorization": "Bearer test-token"})),
        ok,
    )
    assert allowed.status_code == 200
    _auth_tokens.pop("test-token", None)
