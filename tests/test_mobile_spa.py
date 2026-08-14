"""Regression: /mobile/ must serve the mobile SPA, not a JSON 404.

When the desktop frontend has been built (``frontend/dist`` exists — the
Docker image always does this), the gateway used to mount that SPA at ``/``
*before* the standalone mobile app. Starlette's ``/`` mount matches every
path, so ``/mobile/`` and ``/mobile`` were swallowed and FastAPI returned
``{"detail":"Not Found"}`` instead of the phone chat UI.

These tests reconstruct that production layout with a throwaway desktop
``dist`` plus the real ``mobile/`` tree. No network, Docker, or LLM.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gateway.static_assets import DEFAULT_MOBILE_DIR, mount_static_assets

_MOBILE_MARKER = "AuroraCoder — Mobile"
_DESKTOP_MARKER = "DESKTOP_SPA_SENTINEL"


@pytest.fixture
def spa_client(tmp_path: Path) -> TestClient:
    frontend_dist = tmp_path / "dist"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text(
        f"<html><title>Desktop</title>{_DESKTOP_MARKER}</html>",
        encoding="utf-8",
    )
    assert DEFAULT_MOBILE_DIR.is_dir(), "repo mobile/ tree is required"

    app = FastAPI()
    mount_static_assets(app, frontend_dir=frontend_dist, mobile_dir=DEFAULT_MOBILE_DIR)
    return TestClient(app)


def test_mobile_slash_serves_spa_not_json_404(spa_client: TestClient):
    response = spa_client.get("/mobile/", follow_redirects=False)
    assert response.status_code == 200, response.text
    assert "application/json" not in (response.headers.get("content-type") or "")
    assert "Not Found" not in response.text
    assert _MOBILE_MARKER in response.text
    assert _DESKTOP_MARKER not in response.text
    assert "login-screen" in response.text


def test_mobile_no_slash_reaches_spa(spa_client: TestClient):
    response = spa_client.get("/mobile", follow_redirects=True)
    assert response.status_code == 200, response.text
    assert _MOBILE_MARKER in response.text
    assert _DESKTOP_MARKER not in response.text


def test_m_shortcut_redirects_to_mobile_spa(spa_client: TestClient):
    redirect = spa_client.get("/m", follow_redirects=False)
    assert redirect.status_code in (301, 302, 303, 307, 308)
    assert redirect.headers.get("location", "").endswith("/mobile/")

    page = spa_client.get("/m", follow_redirects=True)
    assert page.status_code == 200
    assert _MOBILE_MARKER in page.text


def test_mobile_assets_and_desktop_root_still_served(spa_client: TestClient):
    css = spa_client.get("/mobile/css/mobile.css")
    assert css.status_code == 200
    assert "text/css" in (css.headers.get("content-type") or "")

    desktop = spa_client.get("/")
    assert desktop.status_code == 200
    assert _DESKTOP_MARKER in desktop.text
