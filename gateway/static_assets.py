"""Desktop SPA and standalone mobile-app static mounts.

Starlette matches mounts in registration order. A mount at ``/`` matches
every path, so the mobile app and its shortcuts must be registered first;
otherwise ``/mobile/`` is swallowed and FastAPI returns JSON 404.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FRONTEND_DIR = _REPO_ROOT / "frontend" / "dist"
DEFAULT_MOBILE_DIR = _REPO_ROOT / "mobile"


def mount_static_assets(
    application: FastAPI,
    frontend_dir: Path | str | None = None,
    mobile_dir: Path | str | None = None,
) -> None:
    """Mount the mobile SPA, ``/m`` shortcut, then the desktop SPA catch-all."""
    frontend_dir = Path(frontend_dir) if frontend_dir is not None else DEFAULT_FRONTEND_DIR
    mobile_dir = Path(mobile_dir) if mobile_dir is not None else DEFAULT_MOBILE_DIR

    if mobile_dir.exists():
        @application.get("/mobile")
        async def mobile_no_slash():
            return RedirectResponse(url="/mobile/")

        application.mount(
            "/mobile",
            StaticFiles(directory=str(mobile_dir), html=True),
            name="mobile",
        )

        @application.get("/m")
        async def mobile_shortcut():
            return RedirectResponse(url="/mobile/")

    if frontend_dir.exists():
        application.mount(
            "/",
            StaticFiles(directory=str(frontend_dir), html=True),
            name="frontend",
        )
