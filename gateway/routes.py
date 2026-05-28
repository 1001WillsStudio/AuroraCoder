"""
REST + SSE route handlers for the Conversation Gateway.

All endpoint definitions live here and are registered on the FastAPI *app*
created in ``gateway.api``.  Because ``api.py`` imports this module at the
bottom (after ``app`` is created), the ``from gateway.api import app``
circular import works safely at import time.
"""

import asyncio
import io
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Optional, Dict, Any, List

import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse

from pydantic import BaseModel, Field

from src.config import WORKSPACE_DIR
from src.code_sandbox import shell

from gateway.conversation_store import store, strip_task_instruction
from gateway.settings_store import (
    get_all_settings,
    update_settings as _store_update_settings,
    configure_github_auth,
)
from gateway.provider_registry import (
    get_available_providers,
    get_default_provider,
    sync_tool_env_vars,
)
from gateway.workspace import (
    clear_conversation_snapshots,
    get_file_diffs_for_conversation,
    build_file_tree,
    count_workspace_files,
)
from gateway.streaming import (
    ActiveStream,
    active_streams,
    _streams_lock,
    _cancel_active_stream,
    _has_active_main_stream,
    _proxy_backend_stream,
    _subscriber_sse,
    _format_sse,
)

# Import app after stream deps are resolved — app already exists in api.py's
# namespace by the time api.py does ``from gateway import routes``.
from gateway.api import app

logger = logging.getLogger(__name__)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8080")


# ============================================================================
# Pydantic Models
# ============================================================================

class ConversationSave(BaseModel):
    """Payload for directly saving a conversation (PUT)."""
    messages: List[Dict[str, Any]] = Field(..., description="Full message list")
    status: str = Field("completed")
    provider_id: Optional[str] = None
    parent_id: Optional[str] = None
    session_id: Optional[str] = None
    type: str = Field("user_chat")


class _SettingsUpdate(BaseModel):
    """Partial update for settings."""
    api_keys: Optional[dict] = None
    provider_overrides: Optional[dict] = None
    custom_providers: Optional[list] = None
    other: Optional[dict] = None


class DeleteRequest(BaseModel):
    """Request body for deleting a file or folder."""
    path: str = Field(..., description="Relative path within the workspace")


def _get_workspace() -> Optional[Path]:
    """Return the workspace directory (``/workspace`` in Docker, None otherwise)."""
    if WORKSPACE_DIR:
        p = Path(WORKSPACE_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return None


# ============================================================================
# Endpoints — Streaming
# ============================================================================

@app.post("/api/chat")
async def proxy_chat(request: Request):
    """Proxy a chat request to the backend.

    Accepts the same body as the backend's ``POST /api/chat``.
    The backend connection is maintained by this server — the frontend
    can disconnect and reconnect without losing the stream.
    """
    t0 = time.perf_counter()
    body = await request.json()
    t1 = time.perf_counter()
    body_size = len(json.dumps(body)) if body else 0
    cid_tag = (body.get('conversation_id') or 'new')[:8]
    logger.info(f"[proxy] [{cid_tag}...] json_parse={t1-t0:.3f}s body_size={body_size}")
    conversation_id = body.get("conversation_id") or str(uuid.uuid4())
    body["conversation_id"] = conversation_id

    # Extract conversation-server metadata (not forwarded to backend)
    conv_type = body.pop("conv_type", "user_chat")
    parent_id = body.pop("parent_id", None)
    tool_call_id = body.pop("tool_call_id", None)

    # 409 guard: only for user_chat (subagents are allowed alongside a main stream)
    if conv_type == "user_chat":
        existing = await _has_active_main_stream(exclude=conversation_id)
        if existing:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "Another conversation is still running",
                    "active_conversation_id": existing,
                },
            )

    # Cancel any previous stream for the SAME conversation (re-send / continue)
    t2 = time.perf_counter()
    await _cancel_active_stream(conversation_id)
    t3 = time.perf_counter()
    logger.info(f"[proxy] [{cid_tag}...] cancel_stream={t3-t2:.3f}s")

    stream = ActiveStream(
        conversation_id=conversation_id,
        conv_type=conv_type,
        parent_id=parent_id,
        tool_call_id=tool_call_id,
        provider=body.get("provider"),
    )

    t4 = time.perf_counter()
    store.create_conversation(
        conversation_id=conversation_id,
        provider_id=body.get("provider"),
        conv_type=conv_type,
        parent_id=parent_id,
    )

    # Seed frontend_messages so loading the conversation before the first
    # backend event still shows something.  For existing conversations
    # (re-send / interrupt), APPEND instead of replacing — otherwise the
    # entire history is lost if the backend never produces events.
    if body.get("message"):
        clean_content = strip_task_instruction(body["message"]) or body["message"]
        new_user_msg = {"role": "user", "content": clean_content.strip()}
        existing_fe_msgs = store.get_frontend_messages(conversation_id)
        if existing_fe_msgs:
            existing_fe_msgs.append(new_user_msg)
            store.save_frontend_messages(conversation_id, existing_fe_msgs)
        else:
            store.save_frontend_messages(conversation_id, [new_user_msg])
    t5 = time.perf_counter()
    logger.info(f"[proxy] [{cid_tag}...] store_ops={t5-t4:.3f}s")

    async with _streams_lock:
        active_streams[conversation_id] = stream

    # Notify the parent stream immediately so the subagent shows in the sidebar
    if parent_id:
        parent_stream = active_streams.get(parent_id)
        if parent_stream and not parent_stream.finished:
            evt = ("subagent_event", {
                "child_id": conversation_id,
                "tool_call_id": tool_call_id,
                "event_type": "started",
                "status": "running",
            })
            for q in list(parent_stream.subscribers):
                try:
                    q.put_nowait(evt)
                except asyncio.QueueFull:
                    pass

    stream.task = asyncio.create_task(_proxy_backend_stream(stream, body))

    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    stream.subscribers.append(queue)

    t_total = time.perf_counter() - t0
    logger.info(f"[proxy] [{cid_tag}...] total_pre_backend={t_total:.3f}s json={t1-t0:.3f}s cancel={t3-t2:.3f}s store={t5-t4:.3f}s")

    return StreamingResponse(
        _subscriber_sse(stream, queue, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Conversation-ID": conversation_id,
        },
    )


@app.get("/api/conversations/{conversation_id}/stream")
async def resume_stream(conversation_id: str, request: Request):
    """Attach to an in-progress stream (mid-stream resume)."""
    stream = active_streams.get(conversation_id)

    if stream:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        stream.subscribers.append(queue)
        return StreamingResponse(
            _subscriber_sse(stream, queue, request, replay_latest=True),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Conversation-ID": conversation_id,
            },
        )

    # No active stream — serve the stored conversation as a done event
    try:
        conv = store.get_conversation(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No active or stored conversation")

    frontend_msgs = store.get_frontend_messages(conversation_id)

    async def _replay():
        yield _format_sse("done", {
            "conversation_id": conversation_id,
            "status": conv.get("status", "completed"),
            "messages": frontend_msgs or conv.get("messages", []),
            "raw_messages": conv.get("messages", []),
        })

    return StreamingResponse(
        _replay(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Conversation-ID": conversation_id,
        },
    )


@app.post("/api/conversations/{conversation_id}/cancel")
async def cancel_conversation_stream(conversation_id: str):
    """Cancel an in-progress stream and persist the current state."""
    stream = active_streams.get(conversation_id)
    if not stream or stream.finished:
        raise HTTPException(status_code=404, detail="No active stream for this conversation")
    await _cancel_active_stream(conversation_id)
    return {"cancelled": conversation_id}


@app.get("/api/conversations/active")
async def list_active_streams():
    """List conversation IDs that are currently streaming."""
    return {
        "active": [
            {
                "conversation_id": s.conversation_id,
                "status": s.status,
                "conv_type": s.conv_type,
                "parent_id": s.parent_id,
            }
            for s in active_streams.values()
            if not s.finished
        ]
    }


# ============================================================================
# Endpoints — Providers, Settings, ToolStore
# ============================================================================

@app.get("/api/providers")
async def list_providers():
    """Return available model providers (built-in + custom)."""
    return {"providers": get_available_providers(), "default": get_default_provider()}


@app.get("/api/settings")
async def get_settings():
    """Return current user settings."""
    settings = get_all_settings()
    settings.setdefault("api_keys", {})
    settings.setdefault("provider_overrides", {})
    settings.setdefault("custom_providers", [])
    settings.setdefault("other", {})
    return settings


@app.put("/api/settings")
async def update_settings(update: _SettingsUpdate):
    """Merge partial settings update and persist."""
    payload = {}
    if update.api_keys is not None:
        payload["api_keys"] = update.api_keys
    if update.provider_overrides is not None:
        payload["provider_overrides"] = update.provider_overrides
    if update.custom_providers is not None:
        payload["custom_providers"] = update.custom_providers
    if update.other is not None:
        payload["other"] = update.other
    result = _store_update_settings(payload)
    sync_tool_env_vars()

    # ── GitHub PAT: auto-configure git (only when github key provided) ─
    if "github" in (update.api_keys or {}):
        configure_github_auth()

    try:
        async with httpx.AsyncClient() as c:
            await c.post(f"{BACKEND_URL}/api/reload", timeout=5)
    except Exception:
        logger.warning("Backend not reachable — provider reload skipped")
    return result


def _count_tools_in_dict(tools_dict: dict, by_source: dict) -> int:
    """Count tools in a {name: {source: ...}} dict, adding to by_source."""
    added = 0
    for name, t in tools_dict.items():
        if not isinstance(t, dict):
            continue
        src = t.get("source", "unknown")
        if src.startswith("mcp:"):
            src = "mcp"
        elif src.startswith("skill:"):
            src = "skill"
        elif src == "public":
            src = "registry"
        by_source[src] = by_source.get(src, 0) + 1
        added += 1
    return added


@app.get("/api/toolstore/status")
async def get_toolstore_status():
    """Return tool counts — registry (online) + MCP/skills (local)."""
    import json as _json
    from pathlib import Path as _Path

    by_source: dict = {}
    total = 0

    try:
        from toolstore.config_manager import ConfigManager as _CM
        home = _Path(_CM().config_dir)
    except ImportError:
        home = _Path(os.environ.get("TOOLSTORE_HOME", os.path.expanduser("~/.toolstore")))

    index_path = home / "index.json"
    if index_path.exists():
        try:
            data = _json.loads(index_path.read_text())
            tools = data.get("tools", {}) if isinstance(data, dict) else {}
            total += _count_tools_in_dict(tools, by_source)
        except Exception:
            pass

    config_path = home / "config.json"
    if config_path.exists():
        try:
            cfg = _json.loads(config_path.read_text())
            local_tools = cfg.get("tools", {}) if isinstance(cfg, dict) else {}
            total += _count_tools_in_dict(local_tools, by_source)
        except Exception:
            pass

    return {"total": total, "by_source": by_source, "available": True}


@app.post("/api/toolstore/refresh")
async def refresh_toolstore():
    """Run `toolstore update` to rebuild the local tool index."""
    import subprocess as _sp
    try:
        from toolstore.config_manager import ConfigManager as _CM
        _home = str(_CM().config_dir)
    except ImportError:
        _home = os.environ.get("TOOLSTORE_HOME", os.path.expanduser("~/.toolstore"))
    try:
        result = _sp.run(
            ["toolstore", "update"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "TOOLSTORE_HOME": _home}
        )
        return {"ok": True, "output": result.stdout[-2000:], "errors": result.stderr[-500:] if result.returncode != 0 else None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ============================================================================
# Endpoints — Workspace info + Conversation CRUD
# ============================================================================

@app.get("/api/workspace")
async def get_workspace_info():
    """Return basic workspace info."""
    from src.code_sandbox import WORKSPACE as ws
    return {
        "workspace": str(ws),
        "shell_alive": shell.is_alive,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": "conversation-history"}


@app.put("/api/conversations/{conversation_id}")
async def save_conversation(conversation_id: str, body: ConversationSave):
    """Create or overwrite a conversation (full replacement)."""
    store.create_conversation(
        conversation_id=conversation_id,
        parent_id=body.parent_id,
        session_id=body.session_id,
        provider_id=body.provider_id,
        conv_type=body.type,
    )
    store.save_messages(conversation_id, body.messages)
    store.update_status(conversation_id, body.status)
    return {"id": conversation_id, "status": body.status}


@app.get("/api/conversations")
async def list_conversations(
    type: Optional[str] = None,
    session_id: Optional[str] = None,
    parent_id: Optional[str] = None,
):
    """List conversations (metadata only, no messages)."""
    items = store.list_conversations(
        conv_type=type,
        parent_id=parent_id,
        session_id=session_id,
    )
    return {"conversations": items}


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """Return full conversation: metadata + messages + frontend_messages."""
    try:
        conv = store.get_conversation(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv["frontend_messages"] = store.get_frontend_messages(conversation_id)
    return conv


@app.get("/api/conversations/{conversation_id}/children")
async def get_conversation_children(conversation_id: str):
    """List child conversations (subagents) spawned by this conversation."""
    children = store.get_children(conversation_id)
    return {"children": children}


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """Delete a conversation and its stored messages."""
    try:
        store.delete_conversation(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"deleted": conversation_id}


# ============================================================================
# Endpoints — File Display (diff, tree, read)
# ============================================================================

@app.get("/api/files/diff")
async def get_file_diff(conversation_id: Optional[str] = None, file_path: Optional[str] = None):
    """Get diff for files modified since the start of the current conversation turn."""
    if not conversation_id:
        return {"files": [], "error": "No conversation_id specified"}

    work_dir = _get_workspace()
    result = get_file_diffs_for_conversation(conversation_id, work_dir)

    if file_path and result["files"]:
        result["files"] = [f for f in result["files"] if f["path"] == file_path]

    return result


@app.post("/api/files/snapshot")
async def create_snapshot(conversation_id: str):
    """Create a new baseline snapshot for the conversation."""
    clear_conversation_snapshots(conversation_id)
    return {"status": "success", "message": "Snapshots cleared for new turn"}


@app.get("/api/files/tree")
async def get_file_tree(max_depth: int = 5):
    """Get the folder structure of the agent's working space."""
    work_dir = _get_workspace()
    if not work_dir or not work_dir.exists():
        return {"tree": [], "root": None, "error": "No active session"}

    tree = build_file_tree(work_dir, work_dir, max_depth=max_depth)
    return {"tree": tree, "root": str(work_dir), "error": None}


@app.get("/api/files/read")
async def read_file_content(file_path: str):
    """Read content of a file from the agent's working space."""
    work_dir = _get_workspace()
    if not work_dir or not work_dir.exists():
        raise HTTPException(status_code=400, detail="No active session")

    try:
        full_path = (work_dir / file_path).resolve()
        if not str(full_path).startswith(str(work_dir.resolve())):
            raise HTTPException(status_code=403, detail="Access denied: path outside working directory")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
        return {"path": file_path, "content": content, "size": full_path.stat().st_size}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {str(e)}")


# ============================================================================
# Endpoints — Workspace (upload, delete, download, export, info)
# ============================================================================

@app.post("/api/workspace/upload")
async def upload_workspace(
    archive: UploadFile = File(...),
    project_name: str = Form("project"),
):
    """Upload a zip archive of a project into the agent workspace."""
    work_dir = _get_workspace()
    if not work_dir:
        raise HTTPException(status_code=400, detail="No active workspace")

    safe_name = Path(project_name).name
    if not safe_name:
        safe_name = "project"
    project_dir = work_dir / safe_name
    project_dir.mkdir(parents=True, exist_ok=True)

    content = await archive.read()
    count = 0

    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue

            safe_path = Path(info.filename)
            if safe_path.is_absolute() or ".." in safe_path.parts:
                logger.warning(f"[upload] Rejected unsafe path: {info.filename}")
                continue

            dest = project_dir / safe_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(info))
            count += 1

    return {
        "status": "success",
        "files_uploaded": count,
        "project": safe_name,
        "workspace": str(work_dir),
    }


@app.post("/api/files/delete")
async def delete_workspace_item(req: DeleteRequest):
    """Delete a file or folder from the agent workspace."""
    work_dir = _get_workspace()
    if not work_dir or not work_dir.exists():
        raise HTTPException(status_code=400, detail="No active session")

    try:
        full_path = (work_dir / req.path).resolve()
        if not str(full_path).startswith(str(work_dir.resolve())):
            raise HTTPException(status_code=403, detail="Access denied: path outside working directory")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    try:
        if full_path.is_dir():
            shutil.rmtree(full_path)
        else:
            full_path.unlink()
        return {"status": "deleted", "path": req.path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete: {str(e)}")


@app.get("/api/files/download")
async def download_workspace_file(file_path: str):
    """Download a file from the agent workspace."""
    work_dir = _get_workspace()
    if not work_dir or not work_dir.exists():
        raise HTTPException(status_code=400, detail="No active session")

    try:
        full_path = (work_dir / file_path).resolve()
        if not str(full_path).startswith(str(work_dir.resolve())):
            raise HTTPException(status_code=403, detail="Access denied: path outside working directory")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file (use export for folders)")

    return FileResponse(path=str(full_path), filename=full_path.name, media_type="application/octet-stream")


@app.get("/api/files/export")
async def export_workspace_folder(folder_path: str):
    """Export a folder from the workspace as a .zip archive."""
    work_dir = _get_workspace()
    if not work_dir or not work_dir.exists():
        raise HTTPException(status_code=400, detail="No active session")

    try:
        full_path = (work_dir / folder_path).resolve()
        if not str(full_path).startswith(str(work_dir.resolve())):
            raise HTTPException(status_code=403, detail="Access denied: path outside working directory")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not full_path.exists() or not full_path.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")

    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp.close()
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in full_path.rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(full_path))
        return FileResponse(path=tmp.name, filename=f"{full_path.name}.zip", media_type="application/zip", background=None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to export: {str(e)}")


@app.get("/api/workspace/info")
async def workspace_info():
    """Return metadata about the current workspace."""
    work_dir = _get_workspace()
    file_count = count_workspace_files(work_dir)
    return {
        "workspace": str(work_dir) if work_dir else None,
        "file_count": file_count,
    }
