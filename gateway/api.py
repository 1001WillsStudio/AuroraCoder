"""
Conversation Gateway — SSE proxy + conversation storage + file display.

Sits between the frontend and the agent backend as an independent gate:

    Frontend (:3000)  ←proxy→  Gateway (8081, internal)  ←SSE→  Backend (8080)
                           ↕
                   data/conversations/

Start with::

    uvicorn gateway.api:app --host 0.0.0.0 --port 8081
"""

import asyncio
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
from starlette.exceptions import HTTPException as StarletteHTTPException

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
    cached settings, ping backend to reload providers, and start the Gap
    Engine's periodic sweep task.

    The sweep task is started unconditionally — it no-ops on every tick
    when memory / heavy ops is disabled for this install (the common
    case), so there's no need to gate the ``create_task`` call itself on
    any setting; see ``memory/ops/gap_scheduler.py``.
    """
    sync_tool_env_vars()
    configure_github_auth()
    try:
        async with httpx.AsyncClient() as c:
            await c.post(f"{BACKEND_URL}/api/reload", timeout=5)
    except Exception:
        logger.warning("Backend not reachable at startup — will sync on first request")

    from memory.ops.gap_scheduler import run_periodic_gap_sweep
    asyncio.create_task(run_periodic_gap_sweep())


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
# Task Instruction Persistence
# ============================================================================
# Stored server-side so task instructions follow the instance, not the browser
# port. This prevents cross-contamination when two AuroraCoder instances
# are running on the same host with different ports.

_TASK_INSTRUCTION_PATH = Path(
    os.environ.get("AURORACODER_DATA_DIR", "/app/data")
) / "task_instruction.txt"


class TaskInstructionRequest(BaseModel):
    instruction: str


@app.get("/api/task-instruction")
async def get_task_instruction():
    """Return the persisted task instruction, or empty string if none."""
    try:
        if _TASK_INSTRUCTION_PATH.exists():
            return {"instruction": _TASK_INSTRUCTION_PATH.read_text(encoding="utf-8")}
    except OSError:
        pass
    return {"instruction": ""}


@app.put("/api/task-instruction")
async def set_task_instruction(body: TaskInstructionRequest):
    """Persist a task instruction on the server filesystem."""
    try:
        _TASK_INSTRUCTION_PATH.parent.mkdir(parents=True, exist_ok=True)
        # atomic write: temp file + rename
        fd, tmp = __import__("tempfile").mkstemp(
            dir=str(_TASK_INSTRUCTION_PATH.parent),
            prefix=".tmp_task_",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body.instruction)
            os.replace(tmp, str(_TASK_INSTRUCTION_PATH))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return {"ok": True}
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save task instruction: {e}")


# ============================================================================
# Instance Identity
# ============================================================================

_AURORACODER_GPU = os.environ.get("AURORACODER_GPU", "0").strip() == "1"


@app.get("/api/instance-info")
async def instance_info():
    """Return instance type so the frontend can label tabs correctly."""
    return {
        "type": "gpu" if _AURORACODER_GPU else "normal",
        "version": "1.0.0",
    }


# ============================================================================
# Register all REST + SSE route handlers
# ============================================================================
# Must be imported BEFORE the static mounts below so that API routes take
# priority over the catch-all frontend static mount at "/".

from gateway import routes  # noqa: E402, F401 — registers routes on `app`

# ============================================================================
# Serve Static Assets
# ============================================================================

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_FRONTEND_DIR = _REPO_ROOT / "frontend" / "dist"
_DEFAULT_MOBILE_DIR = _REPO_ROOT / "mobile"


class DesktopStaticFiles(StaticFiles):
    """Desktop SPA mount. Experimental mobile is a 404 fallback only.

    Successful desktop hits (``/``, assets, the SPA) take the same
    ``StaticFiles.get_response`` path as before. ``/m`` and ``/mobile``
    are answered only after that lookup misses — so mobile is up on
    demand and never sits in front of the desktop matcher, the app
    middleware stack, or API/SSE routes.
    """

    def __init__(self, *args, mobile_dir: str | Path, **kwargs):
        super().__init__(*args, **kwargs)
        self.mobile_dir = str(mobile_dir)
        self._mobile: StaticFiles | None = None

    def _mobile_files(self) -> StaticFiles:
        if self._mobile is None:
            self._mobile = StaticFiles(directory=self.mobile_dir, html=True)
        return self._mobile

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            if path in ("m", "m/"):
                return RedirectResponse(url="/mobile/", status_code=307)
            if path == "mobile" or path.startswith("mobile/"):
                rel = path[len("mobile") :].lstrip("/")
                return await self._mobile_files().get_response(rel, scope)
            raise


def mount_static_assets(
    application: FastAPI,
    frontend_dir: Path | None = None,
    mobile_dir: Path | None = None,
) -> None:
    """Mount the desktop SPA at ``/``. Mobile is experimental and on-demand.

    Desktop is registered first and is the only static mount — same as
    the original layout, so general WebUI routing is unchanged. No
    middleware is added: API, SSE, and successful desktop responses
    never enter mobile code. When ``mobile/`` is absent the mount is
    plain ``StaticFiles``, identical to an install without mobile.

    ``/m`` 404s on the desktop tree (there is no file named ``m``), and
    that miss is what brings mobile up. Without the fallback, FastAPI
    returns ``{"detail":"Not Found"}`` — the Settings link failure.
    """
    if frontend_dir is None:
        frontend_dir = _DEFAULT_FRONTEND_DIR
    if mobile_dir is None:
        mobile_dir = _DEFAULT_MOBILE_DIR

    if frontend_dir.exists():
        if mobile_dir.exists():
            static_app = DesktopStaticFiles(
                directory=str(frontend_dir),
                html=True,
                mobile_dir=mobile_dir,
            )
        else:
            static_app = StaticFiles(directory=str(frontend_dir), html=True)
        application.mount("/", static_app, name="frontend")
    elif mobile_dir.exists():
        # No desktop SPA (unbuilt frontend). /m and /mobile can be normal
        # routes — there is no Mount("/") to shadow them and no desktop
        # WebUI to wrap.
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


mount_static_assets(app)


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="warning")
