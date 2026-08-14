"""Regression: Settings '/m' page is off by default and serves only when enabled."""
from __future__ import annotations

import json
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


def _write_settings(path: Path, enabled: bool | None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if enabled is None:
        path.write_text("{}", encoding="utf-8")
    else:
        path.write_text(
            json.dumps({"other": {"m": {"enabled": enabled}}}),
            encoding="utf-8",
        )
    return path


def _client_for(
    frontend: Path, mobile: Path, settings: Path
) -> TestClient:
    app = FastAPI()
    mount_static_assets(
        app, frontend_dir=frontend, mobile_dir=mobile, settings_path=settings
    )
    return TestClient(app, follow_redirects=False)


def test_m_is_off_by_default(tmp_path: Path):
    """No setting, or enabled false: /m is a JSON 404."""
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")

    missing = tmp_path / "no-settings.json"
    off = _write_settings(tmp_path / "off.json", False)
    empty = _write_settings(tmp_path / "empty.json", None)

    for settings in (missing, off, empty):
        client = _client_for(frontend, mobile, settings)
        response = client.get("/m")
        assert response.status_code == 404, settings
        assert response.json() == {"detail": "Not Found"}
        assert client.get("/m/").status_code == 404


def test_m_serves_when_enabled_in_settings(tmp_path: Path):
    """The reported bug, after the page is turned on in Settings."""
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    settings = _write_settings(tmp_path / "on.json", True)
    client = _client_for(frontend, mobile, settings)

    response = client.get("/m")
    assert response.status_code != 404
    assert '{"detail":"Not Found"}' not in response.text
    if response.status_code in (301, 302, 303, 307, 308):
        assert response.headers["location"] == "/m/"

    followed = TestClient(
        client.app, follow_redirects=True
    ).get("/m")
    assert followed.status_code == 200
    assert "MOBILE-WEB-APP" in followed.text
    assert "DESKTOP-SPA" not in followed.text


def test_m_index_and_asset_are_reachable_when_on(tmp_path: Path):
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    (mobile / "css").mkdir()
    (mobile / "css" / "mobile.css").write_text("/* page */", encoding="utf-8")
    settings = _write_settings(tmp_path / "on.json", True)
    client = _client_for(frontend, mobile, settings)

    index = client.get("/m/")
    assert index.status_code == 200
    assert "MOBILE-WEB-APP" in index.text

    asset = client.get("/m/css/mobile.css")
    assert asset.status_code == 200
    assert "page" in asset.text


def test_turning_on_later_starts_serving(tmp_path: Path):
    """The flag is read per request — Save Settings does not need a remount."""
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    settings = _write_settings(tmp_path / "flag.json", False)
    client = _client_for(frontend, mobile, settings)

    assert client.get("/m").status_code == 404
    _write_settings(settings, True)
    response = TestClient(client.app, follow_redirects=True).get("/m")
    assert response.status_code == 200
    assert "MOBILE-WEB-APP" in response.text


def test_desktop_spa_still_served_at_root(tmp_path: Path):
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    (frontend / "assets").mkdir()
    (frontend / "assets" / "app.js").write_text("DESKTOP-ASSET", encoding="utf-8")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    settings = _write_settings(tmp_path / "on.json", True)
    client = _client_for(frontend, mobile, settings)

    root = client.get("/")
    assert root.status_code == 200
    assert "DESKTOP-SPA" in root.text
    assert "MOBILE-WEB-APP" not in root.text
    assert client.get("/assets/app.js").text == "DESKTOP-ASSET"


def test_desktop_root_identical_whether_m_is_on_or_off(tmp_path: Path):
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    on = _write_settings(tmp_path / "on.json", True)
    off = _write_settings(tmp_path / "off.json", False)

    app_on = FastAPI()
    mount_static_assets(app_on, frontend_dir=frontend, mobile_dir=mobile, settings_path=on)
    app_off = FastAPI()
    mount_static_assets(app_off, frontend_dir=frontend, mobile_dir=mobile, settings_path=off)

    with_on = TestClient(app_on).get("/")
    with_off = TestClient(app_off).get("/")
    assert with_on.status_code == with_off.status_code == 200
    assert with_on.text == with_off.text


def test_desktop_mount_is_plain_staticfiles(tmp_path: Path):
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    settings = _write_settings(tmp_path / "off.json", False)
    app = FastAPI()
    mount_static_assets(
        app, frontend_dir=frontend, mobile_dir=mobile, settings_path=settings
    )
    for route in app.routes:
        if getattr(route, "name", None) == "frontend":
            assert type(route.app) is StaticFiles


def test_no_m_route_when_dir_missing(tmp_path: Path):
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    missing = tmp_path / "no-page"
    settings = _write_settings(tmp_path / "on.json", True)
    app = FastAPI()
    mount_static_assets(
        app, frontend_dir=frontend, mobile_dir=missing, settings_path=settings
    )
    assert not any(getattr(r, "path", None) == "/m" for r in app.routes)
    assert TestClient(app).get("/").status_code == 200


def test_m_works_when_frontend_dist_is_missing(tmp_path: Path):
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    missing = tmp_path / "no-frontend"
    settings = _write_settings(tmp_path / "on.json", True)
    app = FastAPI()
    mount_static_assets(
        app, frontend_dir=missing, mobile_dir=mobile, settings_path=settings
    )
    response = TestClient(app, follow_redirects=True).get("/m")
    assert response.status_code == 200
    assert "MOBILE-WEB-APP" in response.text


def test_live_frontend_m_is_off_by_default():
    """Smoke the real frontend app: /m stays 404 until Settings turns it on."""
    from frontend.server import app

    response = TestClient(app, follow_redirects=False).get("/m")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
