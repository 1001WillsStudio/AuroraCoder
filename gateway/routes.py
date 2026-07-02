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
import subprocess
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
from src.code_sandbox import shell, WORKSPACE

try:
    from toolstore.config_manager import ConfigManager
except ImportError:
    ConfigManager = None

from gateway.conversation_store import store, strip_task_instruction
from gateway.settings_store import (
    get_all_settings,
    update_settings as _store_update_settings,
    configure_github_auth,
)
from gateway.provider_registry import (
    get_available_providers,
    get_default_provider,
    get_max_iterations,
    sync_tool_env_vars,
)
from gateway.workspace import (
    clear_conversation_snapshots,
    get_file_diffs_for_conversation,
    get_cached_file_tree,
    generate_workspace_tree_text,
    invalidate_tree_cache,
    count_workspace_files,
)
from gateway.streaming import (
    ActiveStream,
    active_streams,
    _streams_lock,
    _cancel_active_stream,
    _fix_orphan_tool_calls,
    _proxy_backend_stream,
    _subscriber_sse,
    _format_sse,
)
from gateway.memory.store import get_repository as get_memory_repository
from gateway.memory.schema import MemoryItem, MEMORY_PLANES, MEMORY_TYPES
from gateway.memory.stance import build_stance_block
from gateway.memory.retrieval import rank_candidates
from gateway.memory.gap_store import get_gap_ledger, GAP_PRIORITIES, GAP_STRATEGIES
from gateway.memory.ops.dispatcher import dispatch_gap_investigation
from gateway.memory.ops.reviewer import review_candidate, find_similar_existing

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


class RememberRequest(BaseModel):
    """Payload for the ``remember`` tool — one typed memory write."""
    content: str = Field(..., description="The memory itself, in the agent's own words")
    description: str = Field(..., description="One-liner used for relevance ranking")
    plane: str = Field("world", description="'stance' (always injected) or 'world' (retrieved)")
    type: str = Field("project", description="See MEMORY_TYPES")
    scope: str = Field("project", description="'user' (global) or 'project'")
    confidence: str = Field("medium", description="'high' | 'medium' | 'low'")
    provenance: str = Field("agent-stated", description="How this was learned")
    volatile: bool = Field(False, description="True if this fact can go stale")
    ttl_days: Optional[int] = Field(None, description="Re-verify after this many days if volatile")
    supersedes: Optional[str] = Field(None, description="ID of an existing memory this replaces/updates")
    memory_id: Optional[str] = Field(None, description="Reuse this id to update an existing memory in place")


class LogGapRequest(BaseModel):
    """Payload for the ``log_gap`` tool — flag a knowledge gap for later resolution."""
    question: str = Field(..., description="The specific thing the agent didn't know")
    scope: str = Field("project", description="'user' (global) or 'project'")
    priority: str = Field("medium", description="'low' | 'medium' | 'high'")
    strategy: str = Field("ask", description="'self' (cheap to self-investigate later) or 'ask' (needs the user)")


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

    # ── Inject max_iterations from settings if not already set ───
    if "max_iterations" not in body:
        body["max_iterations"] = get_max_iterations()
    conversation_id = body.get("conversation_id") or str(uuid.uuid4())
    body["conversation_id"] = conversation_id

    # ── Generate workspace tree for the agent's system message ──
    # Only on the *first* turn — continuations already carry the tree
    # inside the cached system message.
    if not body.get("messages"):
        try:
            body["workspace_tree"] = generate_workspace_tree_text(WORKSPACE)
        except Exception:
            body["workspace_tree"] = ""
    else:
        body["workspace_tree"] = ""

    # ── Extract conversation-server metadata (not forwarded to backend) ─
    # Must be extracted (and the conversation created) BEFORE we try to
    # save messages below — a forked conversation has a brand-new ID that
    # does not exist in the index yet.
    conv_type = body.pop("conv_type", "user_chat")
    parent_id = body.pop("parent_id", None)
    tool_call_id = body.pop("tool_call_id", None)

    # Subagent inherits parent's provider by default
    if not body.get("provider") and parent_id:
        parent_stream = active_streams.get(parent_id)
        if parent_stream and parent_stream.provider:
            body["provider"] = parent_stream.provider

    # Ensure the conversation record exists (idempotent) before any
    # save_messages call, so forks don't raise KeyError.
    store.create_conversation(
        conversation_id=conversation_id,
        provider_id=body.get("provider"),
        conv_type=conv_type,
        parent_id=parent_id,
    )

    # ── Fix orphan tool calls before forwarding to the backend ────────────
    # If the previous stream was cancelled mid-tool-execution, the
    # conversation history may contain assistant ``tool_calls`` with no
    # matching ``tool`` responses.  The backend's LLM would choke on
    # these, so we inject synthetic "stopped by user" results now.
    if body.get("messages"):
        body["messages"] = _fix_orphan_tool_calls(body["messages"])
        store.save_messages(conversation_id, body["messages"])

    # ────────────────────────────────────────────────────────────────────


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
    """Cancel an in-progress stream and return the latest persisted messages.

    After cancellation the old stream's ``finally`` block persists the
    latest ``raw_messages`` and ``frontend_messages`` to the store.  We
    return them immediately so the frontend can update its state — this
    closes the gap where content streamed via deltas was never persisted
    into the frontend's ``rawMessages`` and would be lost on interrupt.
    """
    await _cancel_active_stream(conversation_id)

    raw_messages = store.get_messages(conversation_id)
    frontend_messages = store.get_frontend_messages(conversation_id)

    return {
        "cancelled": conversation_id,
        "raw_messages": raw_messages,
        "frontend_messages": frontend_messages,
    }




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

    by_source: dict = {}
    total = 0

    try:
        home = Path(ConfigManager().config_dir)
    except ImportError:
        home = Path(os.environ.get("TOOLSTORE_HOME", os.path.expanduser("~/.toolstore")))

    index_path = home / "index.json"
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text())
            tools = data.get("tools", {}) if isinstance(data, dict) else {}
            total += _count_tools_in_dict(tools, by_source)
        except Exception:
            pass

    config_path = home / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            local_tools = cfg.get("tools", {}) if isinstance(cfg, dict) else {}
            total += _count_tools_in_dict(local_tools, by_source)
        except Exception:
            pass

    return {"total": total, "by_source": by_source, "available": True}


@app.post("/api/toolstore/refresh")
async def refresh_toolstore():
    """Run `toolstore update` to rebuild the local tool index."""
    try:
        _home = str(ConfigManager().config_dir)
    except ImportError:
        _home = os.environ.get("TOOLSTORE_HOME", os.path.expanduser("~/.toolstore"))
    try:
        result = subprocess.run(
            ["toolstore", "update"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "TOOLSTORE_HOME": _home}
        )
        return {"ok": True, "output": result.stdout[-2000:], "errors": result.stderr[-500:] if result.returncode != 0 else None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ============================================================================
# Endpoints — Agent Memory
# ============================================================================
# Gateway is the sole owner/writer of the memory store (files + SQLite index
# in DATA_DIR/memory), mirroring how it already exclusively owns the
# conversation store above. The backend (src/) is a thin HTTP client — see
# src/core_tools/memory_client.py — so there is never more than one process
# writing the on-disk store. See docs/code-agent-memory-design.md.

def _memory_enabled() -> bool:
    settings = get_all_settings()
    return bool(settings.get("other", {}).get("memory", {}).get("enabled", True))


@app.get("/api/memory/stance")
async def get_memory_stance(scope: Optional[str] = None):
    """Return the always-injected Stance block for the system prompt.

    Called once per new conversation (session start) by the backend —
    NOT per turn — so this can afford to do real work without hurting
    hot-path latency. Fails open: returns an empty block on any error
    rather than blocking the agent's turn loop.
    """
    if not _memory_enabled():
        return {"stance": ""}
    try:
        repo = get_memory_repository()
        return {"stance": build_stance_block(repo, scope=scope)}
    except Exception:
        logger.exception("[memory] Stance assembly failed — failing open")
        return {"stance": ""}


@app.post("/api/memory/remember")
async def remember_memory(body: RememberRequest):
    """Write (or update, via ``memory_id``) one typed memory.

    Only ever called by the agent's ``remember`` tool — memory writes are
    agent-authored by construction (design doc §7.1 item 4 / §18).

    Gated by a synchronous LLM review (``ops/reviewer.py``) before anything
    is persisted — an in-session ``remember`` call has no lookback/no-op
    bias the way the M2 extraction pass does, so it needs the *higher*
    precision bar per design doc §11, not a lower one. Fails closed: if
    the review call itself fails, the write is rejected, not allowed
    through — the one deliberate exception to this system's fail-open
    default, since silently disabling a moderation gate on error defeats
    its purpose.
    """
    if not _memory_enabled():
        return {"ok": False, "reason": "memory disabled in settings"}

    if body.plane not in MEMORY_PLANES:
        raise HTTPException(status_code=400, detail=f"invalid plane: {body.plane}")
    if body.type not in MEMORY_TYPES:
        raise HTTPException(status_code=400, detail=f"invalid type: {body.type}")

    repo = get_memory_repository()

    candidate = {
        "content": body.content,
        "description": body.description,
        "plane": body.plane,
        "type": body.type,
        "scope": body.scope,
        "confidence": body.confidence,
    }
    similar = find_similar_existing(repo, plane=body.plane, scope=body.scope, description=body.description)
    decision = review_candidate(candidate, similar, is_update=bool(body.memory_id))

    if decision["decision"] != "approve":
        return {"ok": False, "reason": f"rejected by review: {decision.get('reason') or 'no reason given'}"}

    plane = decision.get("adjusted_plane") or body.plane
    confidence = decision.get("adjusted_confidence") or body.confidence
    memory_id = body.memory_id or decision.get("duplicate_of")

    kwargs = dict(
        content=body.content,
        description=body.description,
        plane=plane,
        type=body.type,
        scope=body.scope,
        confidence=confidence,
        provenance=body.provenance,
        volatile=body.volatile,
        ttl_days=body.ttl_days,
        supersedes=body.supersedes,
    )
    if memory_id:
        kwargs["id"] = memory_id

    try:
        item = MemoryItem(**kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    saved = repo.upsert(item)
    return {"ok": True, "id": saved.id}


@app.get("/api/memory/recall")
async def recall_memory(query: str = "", plane: str = "world", scope: Optional[str] = None, k: int = 5):
    """Query-aware retrieval over the World Model (design doc §12)."""
    if not _memory_enabled():
        return {"results": []}
    if plane not in MEMORY_PLANES:
        raise HTTPException(status_code=400, detail=f"invalid plane: {plane}")

    repo = get_memory_repository()
    results = rank_candidates(repo, query=query, plane=plane, scope=scope, k=max(1, min(k, 20)))
    if results:
        repo.bump_usage([r["id"] for r in results])
    return {"results": results}


@app.get("/api/memory")
async def list_memory(plane: Optional[str] = None, scope: Optional[str] = None, limit: int = 200):
    """List memory metadata (no content) — for a future Memory browser UI / debugging."""
    repo = get_memory_repository()
    return {"memories": repo.list(plane=plane, scope=scope, limit=limit)}


@app.delete("/api/memory/{memory_id}")
async def delete_memory(memory_id: str):
    """Delete a memory (human-editable escape hatch — memory files are also
    plain markdown files a user can hand-edit directly on disk)."""
    repo = get_memory_repository()
    if not repo.delete(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": memory_id}


# ============================================================================
# Endpoints — Gap Ledger (design doc §13, Gap Engine — "light" half only)
# ============================================================================
# Logging/listing/resolving gaps is always on (cheap, synchronous). Actively
# *investigating* an open gap is Layer 2b heavy-ops, disabled by default —
# see gateway/memory/ops/dispatcher.py.

@app.post("/api/memory/gaps")
async def log_gap(body: LogGapRequest):
    """Flag a knowledge gap. Only ever called by the agent's ``log_gap`` tool.

    Recurring gaps on the same scope (similar question already open) are
    escalated in priority rather than duplicated — see GapLedger.log_gap.
    """
    if not _memory_enabled():
        return {"ok": False, "reason": "memory disabled in settings"}
    if body.priority not in GAP_PRIORITIES or body.strategy not in GAP_STRATEGIES:
        raise HTTPException(status_code=400, detail="Invalid priority or strategy")
    ledger = get_gap_ledger()
    gap = ledger.log_gap(
        question=body.question, scope=body.scope,
        priority=body.priority, detected_from="agent", strategy=body.strategy,
    )
    return {"ok": True, "gap": gap}


@app.get("/api/memory/gaps")
async def list_gaps(status: Optional[str] = None, scope: Optional[str] = None):
    """List gap ledger entries — for a future Memory/Gaps browser UI."""
    ledger = get_gap_ledger()
    return {"gaps": ledger.list(status=status, scope=scope)}


@app.get("/api/memory/gaps/{gap_id}")
async def get_gap(gap_id: str):
    ledger = get_gap_ledger()
    gap = ledger.get(gap_id)
    if gap is None:
        raise HTTPException(status_code=404, detail="Gap not found")
    return {"gap": gap}


@app.post("/api/memory/gaps/{gap_id}/defer")
async def defer_gap(gap_id: str):
    ledger = get_gap_ledger()
    if not ledger.defer(gap_id):
        raise HTTPException(status_code=404, detail="Gap not found")
    return {"ok": True}


@app.post("/api/memory/gaps/{gap_id}/investigate")
async def investigate_gap(gap_id: str):
    """Dispatch active (heavy-ops) investigation of an open gap.

    No-ops with a clear reason unless ``settings.other.memory.heavy_ops_enabled``
    is explicitly set — this is scaffolding, not a working feature yet.
    """
    result = dispatch_gap_investigation(gap_id)
    if not result.get("ok") and result.get("reason") == "gap not found":
        raise HTTPException(status_code=404, detail="Gap not found")
    return result


# ============================================================================
# Endpoints — Workspace info + Conversation CRUD
# ============================================================================

@app.get("/api/workspace")
async def get_workspace_info():
    """Return basic workspace info."""
    return {
        "workspace": str(WORKSPACE),
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
    """Get the folder structure of the agent's working space.

    Uses a server-side cache (invalidated on file writes + short TTL)
    so that repeated calls during the same streaming session don't
    re-scan the entire workspace.
    """
    work_dir = _get_workspace()
    if not work_dir or not work_dir.exists():
        return {"tree": [], "root": None, "error": "No active session"}

    tree, root, _version = get_cached_file_tree(work_dir, work_dir, max_depth=max_depth)
    return {"tree": tree, "root": root, "error": None}


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

    invalidate_tree_cache()
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
        invalidate_tree_cache()
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
