"""Regression: reload after sending must reopen the same chat.

Explorer (fresh page load): type a prompt, send, wait for the assistant
reply, then reload. The main pane was the empty welcome screen every
time. The chat existed under All History, but the address bar stayed
``/`` so there was nothing to restore. Browser Back after opening a
history item left the app (about:blank) instead of the previous view.

There is no JS test runner here, so behaviour is locked two ways:
  * the extracted helpers in ``frontend/src/utils/conversationUrl.js``
    are executed with Node (hermetic: no network, no DOM);
  * a source scan asserts App.jsx writes ``/c/{id}`` when a conversation
    becomes active, reads it on boot, and handles ``popstate``.
  * the production static server must serve the desktop SPA at
    ``/c/{id}`` so a reload is not a FastAPI JSON 404.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from frontend.server import mount_static_assets

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "frontend" / "src" / "utils" / "conversationUrl.js"
APP = ROOT / "frontend" / "src" / "App.jsx"

CID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _eval_js(expr: str, extra: str = ""):
    script = (
        "import {\n"
        "  parseConversationId,\n"
        "  conversationPath,\n"
        "  syncConversationUrl,\n"
        f"}} from {json.dumps(HELPER.resolve().as_uri())}\n"
        f"{extra}\n"
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


def _write_spa(root: Path, marker: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        f"<!DOCTYPE html><html><body>{marker}</body></html>",
        encoding="utf-8",
    )
    return root


def _history_stub():
    return """
const calls = [];
const history = {
  pushState(state, title, url) { calls.push({ method: 'push', url, state }); },
  replaceState(state, title, url) { calls.push({ method: 'replace', url, state }); },
};
"""


@pytest.mark.unit
def test_root_path_has_no_conversation_to_restore():
    """The reported reload: address bar is ``/``, parse must yield null."""
    assert _eval_js("parseConversationId('/')") is None
    assert _eval_js("parseConversationId('')") is None
    assert _eval_js("parseConversationId('/m')") is None
    assert _eval_js("parseConversationId('/settings')") is None


@pytest.mark.unit
def test_conversation_path_round_trips_the_open_chat():
    """After a send the URL must become /c/{id}; reload reads that id back."""
    path = _eval_js(f"conversationPath({json.dumps(CID)})")
    assert path == f"/c/{CID}"
    assert _eval_js(f"parseConversationId({json.dumps(path)})") == CID
    assert _eval_js("conversationPath(null)") == "/"
    assert _eval_js("conversationPath('')") == "/"


@pytest.mark.unit
def test_sync_replaces_root_when_a_conversation_id_arrives():
    """First SSE conversation_id must write /c/{id} (replace, not a new history row)."""
    extra = _history_stub() + "const location = { pathname: '/' };\n"
    script_expr = (
        f"(function() {{ const mode = syncConversationUrl({json.dumps(CID)}, "
        f"{{ history, location, mode: 'replace' }}); return {{ mode, calls }}; }})()"
    )
    out = _eval_js(script_expr, extra=extra)
    assert out["mode"] == "replace"
    assert out["calls"] == [
        {"method": "replace", "url": f"/c/{CID}", "state": {"conversationId": CID}}
    ]


@pytest.mark.unit
def test_sync_pushes_when_opening_another_chat():
    """History click must push so Back returns to the previous view."""
    extra = _history_stub() + f"const location = {{ pathname: '/c/{CID}' }};\n"
    other = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
    out = _eval_js(
        f"(function() {{ const mode = syncConversationUrl({json.dumps(other)}, "
        f"{{ history, location, mode: 'push' }}); return {{ mode, calls }}; }})()",
        extra=extra,
    )
    assert out["mode"] == "push"
    assert out["calls"][0]["url"] == f"/c/{other}"


@pytest.mark.unit
def test_sync_is_noop_when_url_already_matches():
    extra = _history_stub() + f"const location = {{ pathname: '/c/{CID}' }};\n"
    out = _eval_js(
        f"(function() {{ const mode = syncConversationUrl({json.dumps(CID)}, "
        f"{{ history, location, mode: 'replace' }}); return {{ mode, calls }}; }})()",
        extra=extra,
    )
    assert out["mode"] is None
    assert out["calls"] == []


@pytest.mark.unit
def test_app_restores_conversation_from_the_address_bar():
    """App.jsx used to keep conversationId only in React state (always null on boot)."""
    src = APP.read_text(encoding="utf-8")
    assert "from './utils/conversationUrl.js'" in src or 'from "./utils/conversationUrl.js"' in src
    assert "parseConversationId" in src
    assert "syncConversationUrl" in src
    assert "popstate" in src
    assert "handleLoadConversation" in src
    # Boot path must actually load the id from the URL, not just import the helper.
    assert "skipNavigate" in src
    assert "window.location.pathname" in src or "parseConversationId(" in src


@pytest.mark.unit
def test_app_writes_url_on_send_load_clear_and_fork():
    src = APP.read_text(encoding="utf-8")
    # Implicit id (SSE) uses replace; user navigation uses push.
    assert "mode: 'replace'" in src or 'mode: "replace"' in src
    assert "mode: 'push'" in src or 'mode: "push"' in src
    # New Chat / clear must send the address bar back to `/`.
    clear = src[src.index("const handleClear") : src.index("const handleStop")]
    assert "syncConversationUrl" in clear
    load = src[src.index("const handleLoadConversation") : src.index("// ── Render")]
    assert "syncConversationUrl" in load
    fork = src[src.index("const handleForkConversation") : src.index("const handleForkDismiss")]
    assert "syncConversationUrl" in fork


@pytest.mark.unit
def test_conversation_reload_path_serves_desktop_spa(tmp_path: Path):
    """Reload of /c/{id} must be the React shell, not a JSON 404."""
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    app = FastAPI()
    mount_static_assets(app, frontend_dir=frontend, mobile_dir=mobile)
    client = TestClient(app)

    response = client.get(f"/c/{CID}")
    assert response.status_code == 200, (
        f"/c/{{id}} returned {response.status_code} (body={response.text!r})"
    )
    assert "DESKTOP-SPA" in response.text
    assert '{"detail":"Not Found"}' not in response.text
    assert "MOBILE-WEB-APP" not in response.text


@pytest.mark.unit
def test_conversation_reload_path_does_not_steal_mobile(tmp_path: Path):
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    app = FastAPI()
    mount_static_assets(app, frontend_dir=frontend, mobile_dir=mobile)
    client = TestClient(app, follow_redirects=True)
    response = client.get("/m")
    assert "MOBILE-WEB-APP" in response.text
    assert "DESKTOP-SPA" not in response.text
