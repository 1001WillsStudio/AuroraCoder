"""
Frontend server — serves the React SPA and proxies API calls to the internal gateway.

Runs on port 3000 (exposed to user).  The gateway (port 8081) is internal-only
and handles all the "dirty work": SSE proxy, conversation persistence, auth,
file display, settings, providers, workspace management.

    Browser  ←HTTP/SSE→  Frontend (:3000)  ←proxy→  Gateway (:8081)  ←SSE→  Backend (:8080)
"""

import json
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

GATEWAY_URL = "http://localhost:8081"

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_FRONTEND_DIR = Path(__file__).resolve().parent / "dist"
_DEFAULT_MOBILE_DIR = _REPO_ROOT / "mobile"


def _default_settings_path() -> Path:
    if os.environ.get("AURORACODER_DOCKER", "0") == "1":
        return Path("/app/data/settings.json")
    return Path(
        os.environ.get(
            "AURORACODER_DATA_DIR",
            os.path.expanduser("~/.auroracoder/data"),
        )
    ) / "settings.json"


def _m_is_on(settings_path: Path) -> bool:
    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    if not isinstance(raw, dict):
        return False
    other = raw.get("other")
    if not isinstance(other, dict):
        return False
    page = other.get("m")
    if not isinstance(page, dict):
        return False
    return page.get("enabled") is True

app = FastAPI(title="AuroraCoder Frontend")


# ── Proxy helpers ──────────────────────────────────────────────────────────

async def _proxy(request: Request, target_path: str):
    """Forward a request to the internal gateway and stream the response back.

    Uses ``client.send(..., stream=True)`` so SSE chunks are forwarded in
    real-time rather than buffered.
    """
    url = f"{GATEWAY_URL}{target_path}"
    if request.url.query:
        url += f"?{request.url.query}"

    body = await request.body()
    req_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }

    client = httpx.AsyncClient(timeout=httpx.Timeout(None))
    req = client.build_request(request.method, url, content=body, headers=req_headers)
    resp = await client.send(req, stream=True)

    # Strip hop-by-hop headers so SSE streaming works
    resp_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in ("transfer-encoding", "content-encoding", "content-length")
    }

    async def stream_body():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_body(),
        status_code=resp.status_code,
        headers=resp_headers,
        media_type=resp.headers.get("content-type"),
    )


# ── Proxy routes (matched before the static catch-all) ────────────────────

@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_api(request: Request, path: str):
    return await _proxy(request, f"/api/{path}")


@app.get("/health")
async def proxy_health(request: Request):
    return await _proxy(request, "/health")


def mount_static_assets(
    application: FastAPI,
    frontend_dir: Path | None = None,
    mobile_dir: Path | None = None,
    settings_path: Path | None = None,
) -> None:
    """Desktop at ``/``. ``/m`` is served only when Settings has turned it on."""
    if frontend_dir is None:
        frontend_dir = _DEFAULT_FRONTEND_DIR
    if mobile_dir is None:
        mobile_dir = _DEFAULT_MOBILE_DIR
    if settings_path is None:
        settings_path = _default_settings_path()

    if mobile_dir.exists():
        files = StaticFiles(directory=str(mobile_dir), html=True)

        async def _m_index():
            if not _m_is_on(settings_path):
                raise HTTPException(status_code=404, detail="Not Found")
            return RedirectResponse(url="/m/", status_code=307)

        async def _m_path(path: str, request: Request):
            if not _m_is_on(settings_path):
                raise HTTPException(status_code=404, detail="Not Found")
            return await files.get_response(path, request.scope)

        application.add_api_route("/m", _m_index, methods=["GET", "HEAD"])
        application.add_api_route("/m/{path:path}", _m_path, methods=["GET", "HEAD"])

    if frontend_dir.exists():
        application.mount(
            "/",
            StaticFiles(directory=str(frontend_dir), html=True),
            name="frontend",
        )


mount_static_assets(app)


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000, log_level="warning")
