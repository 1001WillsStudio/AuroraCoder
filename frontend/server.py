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
from fastapi.responses import StreamingResponse
from pathlib import Path

GATEWAY_URL = "http://localhost:8081"

app = FastAPI(title="AuroraCoder Frontend")


# ── Proxy helpers ──────────────────────────────────────────────────────────

async def _proxy(request: Request, target_path: str):
    """Forward a request to the internal gateway and stream the response back."""
    client = httpx.AsyncClient(timeout=httpx.Timeout(None))
    url = f"{GATEWAY_URL}{target_path}"
    if request.url.query:
        url += f"?{request.url.query}"

    body = await request.body()
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }

    resp = await client.request(
        method=request.method,
        url=url,
        content=body,
        headers=headers,
    )

    # Strip hop-by-hop headers so SSE streaming works
    resp_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in ("transfer-encoding", "content-encoding", "content-length")
    }

    return StreamingResponse(
        resp.aiter_bytes(),
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


@app.get("/m")
async def proxy_m_redirect(request: Request):
    return await _proxy(request, "/m")


@app.api_route("/mobile/{path:path}", methods=["GET", "POST"])
async def proxy_mobile(request: Request, path: str):
    return await _proxy(request, f"/mobile/{path}")


# ── Static files (catch-all SPA — mounted last so routes above win) ───────

frontend_dist = Path(__file__).resolve().parent / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000, log_level="warning")
