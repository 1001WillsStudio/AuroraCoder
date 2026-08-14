"""Regression: Settings 'Open mobile web app' (href /m) must serve the mobile UI.

Users hit the frontend on :3000. The gateway (8081) and agent backend (8080)
are not a mobile-specific gateway — ``/m`` there is a 404. Experimental
mobile is served on demand by the user-facing frontend so the desktop
``/`` mount stays plain ``StaticFiles``.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from frontend.server import mount_static_assets


def _write_spa(root: Path, marker: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        f"<!DOCTYPE html><html><body>{marker}</body></html>",
        encoding="utf-8",
    )
    return root


def _client_for(frontend: Path, mobile: Path) -> TestClient:
    app = FastAPI()
    mount_static_assets(app, frontend_dir=frontend, mobile_dir=mobile)
    return TestClient(app, follow_redirects=False)


def _mounted_names(app: FastAPI) -> list[str]:
    return [name for r in app.routes if (name := getattr(r, "name", None))]


def test_m_redirects_to_mobile_on_frontend(tmp_path: Path):
    """The reported bug: GET /m on the user-facing app must not 404 JSON."""
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    client = _client_for(frontend, mobile)

    response = client.get("/m")
    assert response.status_code != 404, (
        f"/m returned 404 (body={response.text!r}); the desktop SPA mount "
        "is shadowing the mobile shortcut"
    )
    assert response.status_code in (301, 302, 303, 307, 308)
    assert response.headers["location"] == "/mobile/"
    assert '{"detail":"Not Found"}' not in response.text


def test_following_m_loads_mobile_chat_ui(tmp_path: Path):
    """Settings opens /m in a new tab; the user must land on the mobile UI."""
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    app = FastAPI()
    mount_static_assets(app, frontend_dir=frontend, mobile_dir=mobile)
    client = TestClient(app, follow_redirects=True)

    response = client.get("/m")
    assert response.status_code == 200
    assert "MOBILE-WEB-APP" in response.text
    assert "DESKTOP-SPA" not in response.text
    assert '{"detail":"Not Found"}' not in response.text


def test_mobile_index_and_asset_are_reachable(tmp_path: Path):
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    (mobile / "css").mkdir()
    (mobile / "css" / "mobile.css").write_text("/* mobile */", encoding="utf-8")
    client = _client_for(frontend, mobile)

    index = client.get("/mobile/")
    assert index.status_code == 200
    assert "MOBILE-WEB-APP" in index.text
    assert "DESKTOP-SPA" not in index.text

    asset = client.get("/mobile/css/mobile.css")
    assert asset.status_code == 200
    assert "mobile" in asset.text


def test_desktop_spa_still_served_at_root(tmp_path: Path):
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    (frontend / "assets").mkdir()
    (frontend / "assets" / "app.js").write_text("DESKTOP-ASSET", encoding="utf-8")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    client = _client_for(frontend, mobile)

    root = client.get("/")
    assert root.status_code == 200
    assert "DESKTOP-SPA" in root.text
    assert "MOBILE-WEB-APP" not in root.text

    asset = client.get("/assets/app.js")
    assert asset.status_code == 200
    assert asset.text == "DESKTOP-ASSET"


def test_desktop_root_identical_with_or_without_mobile(tmp_path: Path):
    """On-demand mobile must not change the desktop `/` response."""
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    missing = tmp_path / "no-mobile"

    app_with = FastAPI()
    mount_static_assets(app_with, frontend_dir=frontend, mobile_dir=mobile)
    app_without = FastAPI()
    mount_static_assets(app_without, frontend_dir=frontend, mobile_dir=missing)

    with_mobile = TestClient(app_with).get("/")
    without_mobile = TestClient(app_without).get("/")
    assert with_mobile.status_code == without_mobile.status_code == 200
    assert with_mobile.text == without_mobile.text
    assert with_mobile.headers["content-type"] == without_mobile.headers["content-type"]


def test_desktop_mount_is_plain_staticfiles(tmp_path: Path):
    """Desktop `/` is not a mobile-aware wrapper."""
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    app = FastAPI()
    mount_static_assets(app, frontend_dir=frontend, mobile_dir=mobile)

    names = _mounted_names(app)
    assert "frontend" in names
    assert "mobile" in names
    for route in app.routes:
        if getattr(route, "name", None) == "frontend":
            assert type(route.app) is StaticFiles


def test_no_mobile_routes_when_mobile_dir_missing(tmp_path: Path):
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    missing = tmp_path / "no-mobile"
    app = FastAPI()
    mount_static_assets(app, frontend_dir=frontend, mobile_dir=missing)

    assert "frontend" in _mounted_names(app)
    assert "mobile" not in _mounted_names(app)
    assert not any(getattr(r, "path", None) == "/m" for r in app.routes)

    response = TestClient(app, follow_redirects=False).get("/")
    assert response.status_code == 200
    assert "DESKTOP-SPA" in response.text


def test_m_works_when_frontend_dist_is_missing(tmp_path: Path):
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    missing = tmp_path / "no-frontend"
    app = FastAPI()
    mount_static_assets(app, frontend_dir=missing, mobile_dir=mobile)
    client = TestClient(app, follow_redirects=True)

    response = client.get("/m")
    assert response.status_code == 200
    assert "MOBILE-WEB-APP" in response.text
    assert '{"detail":"Not Found"}' not in response.text


def test_gateway_style_desktop_mount_404s_on_m(tmp_path: Path):
    """8081 layout: only Mount('/') — /m is FastAPI JSON 404."""
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    app = FastAPI()
    app.mount("/", StaticFiles(directory=str(frontend), html=True), name="frontend")

    response = TestClient(app, follow_redirects=False).get("/m")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_gateway_m_is_404():
    """8081 is not a mobile gateway — /m stays a JSON 404."""
    from gateway.api import app

    assert not any(getattr(r, "path", None) in ("/m", "/mobile") for r in app.routes)
    assert "mobile" not in _mounted_names(app)

    response = TestClient(app, follow_redirects=False).get("/m")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_backend_m_is_404():
    """8080 is not a mobile gateway — /m is unmatched, so FastAPI 404s."""
    import httpx
    from starlette.routing import Match

    from src.web_api.app import app

    assert not any(getattr(r, "path", None) in ("/m", "/mobile") for r in app.routes)
    assert "mobile" not in _mounted_names(app)
    scope = {"type": "http", "path": "/m", "method": "GET"}
    assert all(route.matches(scope)[0] is Match.NONE for route in app.router.routes)

    # Lifespan starts the sandbox; skip it — we only need the 404 matcher.
    transport = httpx.ASGITransport(app=app, lifespan="off")
    with httpx.Client(transport=transport, base_url="http://test") as client:
        response = client.get("/m")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_live_frontend_m_is_not_json_404():
    """Smoke the real frontend app (repo ``mobile/`` is present)."""
    from frontend.server import app

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
