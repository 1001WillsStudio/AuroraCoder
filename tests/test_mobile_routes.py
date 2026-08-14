"""Regression: Settings 'Open mobile web app' (href /m) must serve the mobile UI.

In the production image, ``frontend/dist`` exists (Docker runs ``npm run build``)
and is mounted at ``/``. ``StaticFiles(html=True)`` then looks up a file named
``m``, misses, and FastAPI returns ``{"detail":"Not Found"}`` — the exact body
the explorer agent saw.

Mobile is experimental and must stay off the desktop request path: these tests
rebuild the production layout in tmp dirs and lock (1) ``/m`` works on demand
after a desktop miss and (2) desktop routing is otherwise unchanged — no extra
mount, no app middleware.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from gateway.api import DesktopStaticFiles, mount_static_assets


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
    (frontend / "assets").mkdir()
    (frontend / "assets" / "app.js").write_text("DESKTOP-ASSET", encoding="utf-8")
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    (mobile / "css").mkdir()
    (mobile / "css" / "mobile.css").write_text("/* mobile */", encoding="utf-8")
    return frontend, mobile


def _client_for(frontend: Path, mobile: Path) -> TestClient:
    app = FastAPI()
    mount_static_assets(app, frontend_dir=frontend, mobile_dir=mobile)
    return TestClient(app, follow_redirects=False)


def _mounted_names(app: FastAPI) -> list[str]:
    return [name for r in app.routes if (name := getattr(r, "name", None))]


def _frontend_static(app: FastAPI) -> StaticFiles | None:
    for route in app.routes:
        if getattr(route, "name", None) == "frontend":
            return route.app
    return None


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
    assert "MOBILE-WEB-APP" not in response.text


def test_desktop_is_the_only_static_mount(both_spas):
    """Review: mobile must not sit in the route table in front of the SPA."""
    frontend, mobile = both_spas
    app = FastAPI()
    mount_static_assets(app, frontend_dir=frontend, mobile_dir=mobile)

    names = _mounted_names(app)
    assert "frontend" in names
    assert "mobile" not in names
    assert not any(
        getattr(r, "path", None) in ("/m", "/mobile") for r in app.routes
    )


def test_desktop_unknown_path_is_not_served_as_mobile(both_spas):
    """A non-/m desktop miss must not be served as mobile."""
    frontend, mobile = both_spas
    client = _client_for(frontend, mobile)

    response = client.get("/conversations/abc")
    assert "MOBILE-WEB-APP" not in response.text
    assert response.headers.get("location", "") != "/mobile/"


def test_desktop_static_asset_is_unchanged(both_spas):
    frontend, mobile = both_spas
    client = _client_for(frontend, mobile)

    response = client.get("/assets/app.js")
    assert response.status_code == 200
    assert response.text == "DESKTOP-ASSET"


def test_desktop_api_route_unaffected(both_spas):
    frontend, mobile = both_spas
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"ok": True}

    mount_static_assets(app, frontend_dir=frontend, mobile_dir=mobile)
    client = TestClient(app)

    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert "DESKTOP-SPA" in client.get("/").text


def test_no_mobile_middleware_on_the_app(both_spas):
    """Review: mobile must not wrap the desktop/API/SSE stack."""
    frontend, mobile = both_spas
    app = FastAPI()
    mount_static_assets(app, frontend_dir=frontend, mobile_dir=mobile)
    assert app.user_middleware == []


def test_mobile_files_stay_unbuilt_until_mobile_path(both_spas):
    """On demand: a desktop hit must not construct mobile StaticFiles."""
    frontend, mobile = both_spas
    app = FastAPI()
    mount_static_assets(app, frontend_dir=frontend, mobile_dir=mobile)
    client = TestClient(app, follow_redirects=False)
    spa = _frontend_static(app)
    assert isinstance(spa, DesktopStaticFiles)

    client.get("/")
    assert spa._mobile is None

    client.get("/m")
    assert spa._mobile is None

    client.get("/mobile/")
    assert spa._mobile is not None


def test_desktop_root_identical_with_or_without_mobile(both_spas):
    """Review: enabling on-demand mobile must not change the desktop `/` response."""
    frontend, mobile = both_spas
    missing = mobile.parent / "no-mobile"

    app_with = FastAPI()
    mount_static_assets(app_with, frontend_dir=frontend, mobile_dir=mobile)
    app_without = FastAPI()
    mount_static_assets(app_without, frontend_dir=frontend, mobile_dir=missing)

    with_mobile = TestClient(app_with).get("/")
    without_mobile = TestClient(app_without).get("/")
    assert with_mobile.status_code == without_mobile.status_code == 200
    assert with_mobile.text == without_mobile.text
    assert with_mobile.headers["content-type"] == without_mobile.headers["content-type"]


def test_m_works_when_frontend_dist_is_missing(tmp_path: Path):
    """Unbuilt desktop: /m is a normal route. No desktop WebUI to affect."""
    mobile = _write_spa(tmp_path / "mobile", "MOBILE-WEB-APP")
    missing = tmp_path / "no-frontend"
    app = FastAPI()
    mount_static_assets(app, frontend_dir=missing, mobile_dir=mobile)
    client = TestClient(app, follow_redirects=True)

    assert app.user_middleware == []
    response = client.get("/m")
    assert response.status_code == 200
    assert "MOBILE-WEB-APP" in response.text
    assert '{"detail":"Not Found"}' not in response.text


def test_desktop_only_when_mobile_dir_missing(tmp_path: Path):
    frontend = _write_spa(tmp_path / "frontend", "DESKTOP-SPA")
    missing = tmp_path / "no-mobile"
    app = FastAPI()
    mount_static_assets(app, frontend_dir=frontend, mobile_dir=missing)
    client = TestClient(app, follow_redirects=False)

    assert "frontend" in _mounted_names(app)
    assert "mobile" not in _mounted_names(app)
    assert app.user_middleware == []
    assert isinstance(_frontend_static(app), StaticFiles)
    assert not isinstance(_frontend_static(app), DesktopStaticFiles)

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
