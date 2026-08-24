"""
Frontend server — serves the React SPA and proxies API calls to the internal gateway.

Runs on port 3000 (exposed to user).  The gateway (port 8081) is internal-only
and handles all the "dirty work": SSE proxy, conversation persistence, auth,
file display, settings, providers, workspace management.

    Browser  ←HTTP/SSE→  Frontend (:3000)  ←proxy→  Gateway (:8081)  ←SSE→  Backend (:8080)
"""

from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

GATEWAY_URL = "http://localhost:8081"

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_FRONTEND_DIR = Path(__file__).resolve().parent / "dist"
_DEFAULT_MOBILE_DIR = _REPO_ROOT / "mobile"

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
) -> None:
    """Desktop at ``/``. ``/m`` is the other page when that tree is present."""
    if frontend_dir is None:
        frontend_dir = _DEFAULT_FRONTEND_DIR
    if mobile_dir is None:
        mobile_dir = _DEFAULT_MOBILE_DIR

    if mobile_dir.exists():
        files = StaticFiles(directory=str(mobile_dir), html=True)

        async def _m_index():
            return RedirectResponse(url="/m/", status_code=307)

        async def _m_path(path: str, request: Request):
            return await files.get_response(path, request.scope)

        application.add_api_route("/m", _m_index, methods=["GET", "HEAD"])
        application.add_api_route("/m/{path:path}", _m_path, methods=["GET", "HEAD"])

    if frontend_dir.exists():
        desktop = StaticFiles(directory=str(frontend_dir), html=True)

        # Reload of /c/{id} must serve the SPA. Starlette StaticFiles(html=True)
        # only falls back to index.html for directories, so a conversation
        # path would otherwise be a JSON 404.
        async def _conversation_spa(conversation_id: str, request: Request):
            return await desktop.get_response("index.html", request.scope)

        application.add_api_route(
            "/c/{conversation_id}",
            _conversation_spa,
            methods=["GET", "HEAD"],
        )
        application.mount(
            "/",
            desktop,
            name="frontend",
        )


mount_static_assets(app)


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000, log_level="warning")
