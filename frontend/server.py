"""
Frontend server — serves the React SPA and proxies API calls to the internal gateway.

Runs on port 3000 (exposed to user).  The gateway (port 8081) is internal-only
and handles all the "dirty work": SSE proxy, conversation persistence, auth,
file display, settings, providers, workspace management.

    Browser  ←HTTP/SSE→  Frontend (:3000)  ←proxy→  Gateway (:8081)  ←SSE→  Backend (:8080)
"""

import httpx
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, StreamingResponse
from pathlib import Path

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
    """User-facing static files. Desktop at ``/``; mobile is an alternative page.

    ``/m`` and ``/mobile`` are a second frontend tree, served only when
    ``mobile/`` is present. Desktop ``/`` stays a plain ``StaticFiles``
    mount and never wraps mobile.
    """
    if frontend_dir is None:
        frontend_dir = _DEFAULT_FRONTEND_DIR
    if mobile_dir is None:
        mobile_dir = _DEFAULT_MOBILE_DIR

    if mobile_dir.exists():
        async def _mobile_shortcut():
            return RedirectResponse(url="/mobile/", status_code=307)

        application.add_api_route(
            "/m", _mobile_shortcut, methods=["GET", "HEAD"]
        )
        application.mount(
            "/mobile",
            StaticFiles(directory=str(mobile_dir), html=True),
            name="mobile",
        )

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
