"""Regression: Settings 'Open mobile web app' (href /m) must serve the mobile UI.

In the production image, ``frontend/dist`` exists (Docker runs ``npm run build``)
and used to be mounted at ``/`` *before* ``/m`` and ``/mobile``. Starlette's
catch-all ``Mount("/")`` then swallowed those paths and FastAPI returned
``{"detail":"Not Found"}`` — the exact body the explorer agent saw.

These tests rebuild that production layout in tmp dirs (no network, no
real frontend build) and lock the mount-order contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gateway.api import mount_static_assets


def _write_spa(root: Path, marker: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        f"<!DOCTYPE html><html><body>{marker}</body></html>",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def both_spas(tmp_path: Path) -> tuple[Path, Path]:
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    (mobile / "css").mkdir()
    (mobile / "css" / "mobile.css").write_text("/* mobile */", encoding="utf-8")
    return frontend, mobile


def _client_for(frontend: Path, mobile: Path) -> TestClient:
    app = FastAPI()
    mount_static_assets(app, frontend_dir=frontend, mobile_dir=mobile)
    return TestClient(app, follow_redirects=False)


def test_m_redirects_to_mobile_when_frontend_dist_exists(both_spas):
    """The reported bug: GET /m with a built desktop SPA must not 404 JSON."""
    frontend, mobile = both_spas
    client = _client_for(frontend, mobile)

    response = client.get("/m")
    assert response.status_code != 404, (
        f"/m returned 404 (body={response.text!r}); the desktop SPA mount "
        "is shadowing the mobile shortcut"
    )
    assert response.status_code in (301, 302, 303, 307, 308)
    assert response.headers["location"] == "/mobile/"


def test_m_does_not_return_fastapi_json_404(both_spas):
    """Literal explorer-agent failure: the page body was raw JSON 404."""
    frontend, mobile = both_spas
    client = _client_for(frontend, mobile)

    response = client.get("/m")
    assert response.status_code != 404
    assert '{"detail":"Not Found"}' not in response.text


def test_following_m_loads_mobile_chat_ui(both_spas):
    """Settings opens /m in a new tab; the user must land on the mobile UI."""
    frontend, mobile = both_spas
    app = FastAPI()
    mount_static_assets(app, frontend_dir=frontend, mobile_dir=mobile)
    client = TestClient(app, follow_redirects=True)

    response = client.get("/m")
    assert response.status_code == 200
    assert "MOBILE-WEB-APP" in response.text
    assert "DESKTOP-SPA" not in response.text
    assert '{"detail":"Not Found"}' not in response.text


def test_mobile_index_is_the_chat_ui(both_spas):
    frontend, mobile = both_spas
    client = _client_for(frontend, mobile)

    response = client.get("/mobile/")
    assert response.status_code == 200
    assert "MOBILE-WEB-APP" in response.text
    assert "DESKTOP-SPA" not in response.text


def test_mobile_static_asset_is_reachable(both_spas):
    frontend, mobile = both_spas
    client = _client_for(frontend, mobile)

    response = client.get("/mobile/css/mobile.css")
    assert response.status_code == 200
    assert "mobile" in response.text


def test_desktop_spa_still_served_at_root(both_spas):
    frontend, mobile = both_spas
    client = _client_for(frontend, mobile)

    response = client.get("/")
    assert response.status_code == 200
    assert "DESKTOP-SPA" in response.text


def test_live_gateway_m_is_not_json_404():
    """Smoke the real gateway app (repo ``mobile/`` is present)."""
    from gateway.api import app

    client = TestClient(app, follow_redirects=False)
    response = client.get("/m")
    assert response.status_code != 404
    assert '{"detail":"Not Found"}' not in response.text
    if response.status_code in (301, 302, 303, 307, 308):
        assert response.headers["location"] == "/mobile/"
        followed = client.get("/mobile/")
        assert followed.status_code == 200
        assert "AuroraCoder" in followed.text
        assert "chat-input" in followed.text
