"""
Conversation Gateway — SSE proxy + conversation storage + file display.

Sits between the frontend and the agent backend as an independent gate:

    Frontend (:3000)  ←proxy→  Gateway (8081, internal)  ←SSE→  Backend (8080)
                           ↕
                   data/conversations/

Start with::

    uvicorn gateway.api:app --host 0.0.0.0 --port 8081
"""

import logging
import os
import secrets
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

from gateway.conversation_store import store
from gateway.provider_registry import sync_tool_env_vars
from gateway.settings_store import configure_github_auth

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8080")


# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="Conversation History",
    description="SSE proxy + conversation storage",
    version="1.0.0",
)


@app.on_event("startup")
async def _startup_sync():
    """On boot: sync env vars for src tools, configure GitHub auth from
    cached settings, then ping backend to reload providers."""
    sync_tool_env_vars()
    configure_github_auth()
    try:
        async with httpx.AsyncClient() as c:
            await c.post(f"{BACKEND_URL}/api/reload", timeout=5)
    except Exception:
        logger.warning("Backend not reachable at startup — will sync on first request")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Authentication System
# ============================================================================
# If ACCESS_PASSWORD is set, all /api/* routes require Bearer token auth.
# Tokens are generated server-side on successful login (7-day expiry).

AUTH_PASSWORD = os.environ.get("ACCESS_PASSWORD", "").strip()
_auth_tokens: dict[str, float] = {}
_TOKEN_EXPIRY_SECONDS = 7 * 24 * 3600


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_in_ms: int


def _validate_token(token: str) -> bool:
    if not AUTH_PASSWORD:
        return True
    expiry = _auth_tokens.get(token)
    if expiry is None:
        return False
    if time.time() > expiry:
        _auth_tokens.pop(token, None)
        return False
    return True


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require Bearer token for all /api/* routes (except /api/auth/*, /health).

    If ACCESS_PASSWORD is not set, no auth is required.
    Public paths: /health, /api/auth/*, /mobile/*, /m
    """
    path = request.url.path

    if not AUTH_PASSWORD:
        return await call_next(request)

    public_prefixes = ("/health", "/api/auth/", "/mobile", "/m/")
    if any(path.startswith(p) for p in public_prefixes) or path == "/m":
        return await call_next(request)

    if not path.startswith("/api/"):
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required. Use Bearer token."},
        )

    token = auth_header[7:]
    if not _validate_token(token):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or expired token."},
        )

    return await call_next(request)


@app.post("/api/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    """Authenticate with the access password. Returns a bearer token."""
    if not AUTH_PASSWORD:
        return {"token": "no-auth-needed", "expires_in_ms": _TOKEN_EXPIRY_SECONDS * 1000}

    if body.password != AUTH_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid password.")

    token = secrets.token_hex(32)
    _auth_tokens[token] = time.time() + _TOKEN_EXPIRY_SECONDS
    return {"token": token, "expires_in_ms": _TOKEN_EXPIRY_SECONDS * 1000}


@app.get("/api/auth/check")
async def check_auth(request: Request):
    """Verify that the current auth token is valid."""
    if not AUTH_PASSWORD:
        return {"authenticated": True}

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="No token provided.")

    token = auth_header[7:]
    if not _validate_token(token):
        raise HTTPException(status_code=401, detail="Invalid or expired token.")

    return {"authenticated": True}


# ============================================================================
# Register all REST + SSE route handlers
# ============================================================================
# Must be imported BEFORE the static mounts below so that API routes take
# priority over the catch-all frontend static mount at "/".

from gateway import routes  # noqa: E402, F401 — registers routes on `app`

# ============================================================================
# Serve Static Assets
# ============================================================================

frontend_dir = Path(__file__).resolve().parent.parent / "frontend" / "dist"
mobile_dir = Path(__file__).resolve().parent.parent / "mobile"

if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

if mobile_dir.exists():
    app.mount("/mobile", StaticFiles(directory=str(mobile_dir), html=True), name="mobile")

    @app.get("/m")
    async def mobile_shortcut():
        return RedirectResponse(url="/mobile/")


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="warning")
