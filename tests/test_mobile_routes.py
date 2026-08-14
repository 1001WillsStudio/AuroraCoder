"""Regression: Settings 'Open mobile web app' (href /m) must serve that page."""
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


def test_m_is_not_json_404(tmp_path: Path):
    """The reported bug: GET /m on the user-facing app must not 404 JSON."""
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    client = _client_for(frontend, mobile)

    response = client.get("/m")
    assert response.status_code != 404, (
        f"/m returned 404 (body={response.text!r}); the desktop SPA mount "
        "is shadowing the page"
    )
    assert '{"detail":"Not Found"}' not in response.text
    if response.status_code in (301, 302, 303, 307, 308):
        assert response.headers["location"] == "/m/"


def test_following_m_loads_the_page(tmp_path: Path):
    """Settings opens /m in a new tab; the user must land on that UI."""
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


def test_m_index_and_asset_are_reachable(tmp_path: Path):
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    (mobile / "css").mkdir()
    (mobile / "css" / "mobile.css").write_text("/* page */", encoding="utf-8")
    client = _client_for(frontend, mobile)

    index = client.get("/m/")
    assert index.status_code == 200
    assert "MOBILE-WEB-APP" in index.text
    assert "DESKTOP-SPA" not in index.text

    asset = client.get("/m/css/mobile.css")
    assert asset.status_code == 200
    assert "page" in asset.text


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


def test_desktop_root_identical_with_or_without_the_other_page(tmp_path: Path):
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    missing = tmp_path / "no-page"

    app_with = FastAPI()
    mount_static_assets(app_with, frontend_dir=frontend, mobile_dir=mobile)
    app_without = FastAPI()
    mount_static_assets(app_without, frontend_dir=frontend, mobile_dir=missing)

    with_page = TestClient(app_with).get("/")
    without_page = TestClient(app_without).get("/")
    assert with_page.status_code == without_page.status_code == 200
    assert with_page.text == without_page.text
    assert with_page.headers["content-type"] == without_page.headers["content-type"]


def test_desktop_mount_is_plain_staticfiles(tmp_path: Path):
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    app = FastAPI()
    mount_static_assets(app, frontend_dir=frontend, mobile_dir=mobile)

    names = _mounted_names(app)
    assert "frontend" in names
    assert "m" in names
    assert "mobile" not in names
    for route in app.routes:
        if getattr(route, "name", None) == "frontend":
            assert type(route.app) is StaticFiles


def test_no_m_route_when_dir_missing(tmp_path: Path):
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    missing = tmp_path / "no-page"
    app = FastAPI()
    mount_static_assets(app, frontend_dir=frontend, mobile_dir=missing)

    assert "frontend" in _mounted_names(app)
    assert "m" not in _mounted_names(app)
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


def test_live_frontend_m_is_not_json_404():
    """Smoke the real frontend app (repo page tree is present)."""
    from frontend.server import app

    client = TestClient(app, follow_redirects=True)
    response = client.get("/m")
    assert response.status_code == 200
    assert '{"detail":"Not Found"}' not in response.text
    assert "AuroraCoder" in response.text
    assert "chat-input" in response.text
