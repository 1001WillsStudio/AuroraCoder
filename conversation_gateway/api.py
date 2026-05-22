"""
Conversation Gateway — SSE proxy + conversation storage + file display.

Sits between the frontend and the agent backend as an independent gate:

    Frontend  ←SSE→  Gateway Server (8081)  ←SSE→  Backend (8080)
                           ↕
                   data/conversations/

Key behaviors:
  - Proxies POST /api/chat to the backend, capturing the SSE stream
  - Keeps the backend connection alive even when the frontend disconnects
  - Frontend can reconnect mid-stream via GET /api/conversations/{id}/stream
  - Persists conversations automatically when rounds complete
  - Serves conversation history via REST endpoints
  - Detects continue_as_new_chat tool calls and creates continuation conversations
  - Serves file-display endpoints: diff, tree, read, workspace upload/delete/export

Start with::

    uvicorn conversation_gateway.api:app --host 0.0.0.0 --port 8081
"""

import asyncio
import io
import json
import logging
import os
import time
import uuid
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, List

import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.config import WORKSPACE_DIR
from src.settings_store import (
    get_all_settings,
    get_custom_providers,
    update_settings as _store_update_settings,
)
from src.providers import get_available_providers, get_default_provider, provider_manager
from src.code_sandbox import shell

from .conversation_store import ConversationStore, strip_task_instruction
from .workspace import (
    file_snapshots,
    files_touched,
    snapshot_file,
    mark_file_touched,
    clear_conversation_snapshots,
    compute_unified_diff,
    get_file_diffs_for_conversation,
    build_file_tree,
    clear_workspace,
    count_workspace_files,
    WORKSPACE_EXCLUDE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8080")

store = ConversationStore()


# ============================================================================
# Active Stream Management
# ============================================================================

@dataclass
class ActiveStream:
    """In-memory state for a stream being proxied from the backend."""
    conversation_id: str
    conv_type: str = "user_chat"
    parent_id: Optional[str] = None
    tool_call_id: Optional[str] = None   # set when this is a subagent spawned by a tool call
    provider: Optional[str] = None
    latest_event_type: Optional[str] = None
    latest_event_data: Optional[dict] = None
    latest_frontend_messages: Optional[List[Dict]] = None
    status: str = "running"
    subscribers: list = field(default_factory=list)
    finished: bool = False
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None
    new_conversation_id: Optional[str] = None   # set when continuation detected


active_streams: Dict[str, ActiveStream] = {}
_streams_lock = asyncio.Lock()


_FILE_WRITE_TOOLS = {"write_file", "edit_file", "delete_file"}

# Tool-call IDs for which we've already taken a pre-write snapshot.
# Prevents re-snapshotting after the write has already happened.
_snapshotted_tool_calls: Dict[str, set] = {}  # {conversation_id: {tool_call_id, ...}}


def _fix_orphan_tool_calls(raw_messages: list) -> list:
    """
    Scan *raw_messages* for assistant ``tool_calls`` that have no matching
    ``tool`` response and inject a synthetic "stopped by user" result.

    Returns a new list (always copies) so the caller can decide whether to
    replace the original.
    """
    # Collect all tool_call IDs that already have a response
    responded_ids: set = set()
    for msg in raw_messages:
        if msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id")
            if tc_id:
                responded_ids.add(tc_id)

    # Find orphan tool_calls in assistant messages
    orphans: list[dict] = []
    for msg in raw_messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            tc_id = tc.get("id")
            if tc_id and tc_id not in responded_ids:
                orphans.append(tc)

    if not orphans:
        return list(raw_messages)

    # Build a new list with synthetic tool responses appended
    fixed = list(raw_messages)
    for tc in orphans:
        tool_name = tc.get("function", {}).get("name", "unknown")
        fixed.append({
            "role": "tool",
            "tool_call_id": tc.get("id", ""),
            "name": tool_name,
            "content": json.dumps({
                "status": "stopped",
                "message": f'Tool "{tool_name}" was stopped by the user.',
            }),
        })
    return fixed


def _track_file_changes(conversation_id: str, raw_messages: list):
    """Scan raw_messages for file-modifying tool calls and update the
    gateway's in-memory snapshot/touched tracking so /api/files/diff works.

    Two-phase approach:
      1. When a tool_call appears (no result yet) — snapshot the file
         BEFORE the backend writes it.
      2. When the matching tool_result appears — mark the file as touched
         so the diff endpoint picks it up.
    """
    work_dir = Path(WORKSPACE_DIR) if WORKSPACE_DIR else None
    if not work_dir or not work_dir.exists():
        return

    if conversation_id not in _snapshotted_tool_calls:
        _snapshotted_tool_calls[conversation_id] = set()

    # Collect tool_result IDs so we know which calls have completed
    result_ids = set()
    for msg in raw_messages:
        if msg.get("role") == "tool":
            result_ids.add(msg.get("tool_call_id", ""))

    for msg in raw_messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            name = func.get("name", "")
            if name not in _FILE_WRITE_TOOLS:
                continue
            tc_id = tc.get("id", "")
            try:
                args = json.loads(func.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue
            file_path = args.get("target_file") or args.get("file_path") or ""
            if not file_path:
                continue

            # Phase 1: snapshot BEFORE the write (tool_call seen, no result yet)
            if tc_id not in _snapshotted_tool_calls[conversation_id]:
                _snapshotted_tool_calls[conversation_id].add(tc_id)
                full_path = work_dir / file_path
                if file_path not in file_snapshots.get(conversation_id, {}):
                    try:
                        if full_path.exists() and full_path.is_file():
                            content = full_path.read_text(encoding="utf-8", errors="replace")
                        else:
                            content = ""
                        snapshot_file(conversation_id, file_path, content)
                    except Exception:
                        snapshot_file(conversation_id, file_path, "")

            # Phase 2: mark touched once the tool has completed
            if tc_id in result_ids:
                mark_file_touched(conversation_id, file_path)


async def _cancel_active_stream(conversation_id: str) -> None:
    """Cancel any in-progress stream for a conversation, including child subagent streams.

    All cancellations are initiated concurrently so the total wait is bounded to a
    single timeout regardless of how many subagent children exist.
    """
    t_start = time.perf_counter()
    tasks_to_await: List[asyncio.Task] = []

    async with _streams_lock:
        old = active_streams.get(conversation_id)
        # Find child streams whose parent_id matches this conversation
        children = [
            s for s in active_streams.values()
            if s.parent_id == conversation_id and not s.finished
        ]

    # Cancel child subagent streams — signal all first, then await together
    for child in children:
        logger.info(f"[cancel] Cascading cancel to child {child.conversation_id[:8]}...")
        child.cancel_event.set()
        if child.task and not child.task.done():
            child.task.cancel()
            tasks_to_await.append(child.task)

    # Cancel the parent stream
    if old and not old.finished:
        old.cancel_event.set()
        if old.task and not old.task.done():
            old.task.cancel()
            tasks_to_await.append(old.task)

    # Cancel any continuation child created by this stream
    async with _streams_lock:
        parent = active_streams.get(conversation_id)
        if parent and parent.new_conversation_id:
            continuation = active_streams.get(parent.new_conversation_id)
            if continuation and not continuation.finished:
                logger.info(f"[cancel] Cascading cancel to continuation {continuation.conversation_id[:8]}...")
                continuation.cancel_event.set()
                if continuation.task and not continuation.task.done():
                    continuation.task.cancel()
                    tasks_to_await.append(continuation.task)

    # Await all cancelled tasks concurrently — total wait bounded to 2 s
    if tasks_to_await:
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks_to_await, return_exceptions=True),
                timeout=2.0,
            )
        except (asyncio.TimeoutError, Exception):
            pass
    elapsed = time.perf_counter() - t_start
    if elapsed > 0.1:
        logger.info(f"[cancel] [{conversation_id[:8]}...] duration={elapsed:.3f}s tasks_awaited={len(tasks_to_await)}")


async def _has_active_main_stream(exclude: Optional[str] = None) -> Optional[str]:
    """Return the conversation ID of any running user_chat stream, or None."""
    async with _streams_lock:
        for cid, s in active_streams.items():
            if cid != exclude and s.conv_type == "user_chat" and not s.finished:
                return cid
    return None


# ============================================================================
# SSE Helpers
# ============================================================================

def _format_sse(event_type: str, data: Any) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _parse_sse_blocks(text: str) -> List[tuple]:
    """Parse SSE-formatted text into (event_type, data_dict) pairs."""
    results = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event_type = "message"
        data_str = None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_str = line[5:].strip()
        if data_str:
            try:
                results.append((event_type, json.loads(data_str)))
            except json.JSONDecodeError:
                pass
    return results


# ============================================================================
# Continuation Helpers
# ============================================================================

def _scan_for_continuation(raw_messages: list) -> dict | None:
    """
    Scan assistant messages for a continue_as_new_chat tool call.
    Returns the tool arguments dict, or None.
    """
    for msg in raw_messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            if tc.get("function", {}).get("name") == "continue_as_new_chat":
                try:
                    return json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    return None
    return None


async def _start_continuation(new_cid: str, provider_id: str, user_msg: str):
    """
    Simulate a user opening a new chat, typing the continuation message,
    and pressing Send.  POSTs to the backend via the normal proxy flow.

    This makes the continuation conversation indistinguishable from
    a human-initiated chat — the agent's prompt IS the user message.
    """
    body = {
        "conversation_id": new_cid,
        "message": user_msg,
        "provider": provider_id,
    }

    stream = ActiveStream(
        conversation_id=new_cid,
        conv_type="user_chat",
        parent_id=None,
        provider=provider_id,
    )

    async with _streams_lock:
        active_streams[new_cid] = stream

    stream.task = asyncio.create_task(_proxy_backend_stream(stream, body))

    logger.info(f"[proxy] Continuation {new_cid[:8]}... auto-started")




# ============================================================================
# Backend Proxy — background task
# ============================================================================

async def _proxy_backend_stream(stream: ActiveStream, request_body: dict):
    """
    POST to the backend, read its SSE stream, broadcast to subscribers,
    and persist incrementally after each model round.

    Saves raw_messages every time the message list grows (after each tool
    execution batch), so that if the user stops mid-way the conversation
    is already persisted up to the last completed round.

    This task runs independently of any frontend connection — the backend
    stream stays alive even if all subscribers disconnect.
    """
    cid = stream.conversation_id
    last_saved_msg_count = 0
    backend_connect_start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(None)) as client:
            async with client.stream(
                "POST",
                f"{BACKEND_URL}/api/chat",
                json=request_body,
                headers={"Content-Type": "application/json"},
            ) as response:
                backend_connect_elapsed = time.perf_counter() - backend_connect_start
                logger.info(f"[proxy] [{cid[:8]}...] backend_connect={backend_connect_elapsed:.3f}s status={response.status_code}")
                if response.status_code != 200:
                    body = (await response.aread()).decode(errors="replace")
                    err = {"message": f"Backend returned {response.status_code}: {body[:500]}", "type": "BackendError"}
                    stream.latest_event_type = "error"
                    stream.latest_event_data = err
                    for q in list(stream.subscribers):
                        q.put_nowait(("error", err))
                    return

                buffer = ""
                async for chunk in response.aiter_text():
                    if stream.cancel_event.is_set():
                        break

                    buffer += chunk
                    while "\n\n" in buffer:
                        raw, buffer = buffer.split("\n\n", 1)
                        for etype, edata in _parse_sse_blocks(raw + "\n\n"):
                            stream.latest_event_type = etype
                            stream.latest_event_data = edata

                            # --- Continuation detection ---
                            if not stream.new_conversation_id:
                                args = _scan_for_continuation(edata.get("raw_messages", []))
                                if args:
                                    prompt = args.get("prompt", "")
                                    new_cid = str(uuid.uuid4())

                                    # Build the user message: just the agent's prompt with a note
                                    user_msg = f"[Continued from previous agent session]\n\n{prompt}"

                                    store.create_conversation(
                                        conversation_id=new_cid,
                                        parent_id=cid,
                                        conv_type="user_chat",          # normal chat, not "user_chat_continued"
                                        provider_id=stream.provider,
                                    )
                                    store.save_messages(new_cid, [
                                        {"role": "user", "content": user_msg},
                                    ])
                                    store.save_frontend_messages(new_cid, [
                                        {"role": "user", "content": user_msg},
                                    ])
                                    store.update_status(cid, "continued")
                                    stream.new_conversation_id = new_cid

                                    # Simulate user pressing Send — POST to backend immediately
                                    asyncio.create_task(_start_continuation(new_cid, stream.provider, user_msg))

                                    logger.info(f"[proxy] Created continuation {new_cid[:8]}... from {cid[:8]}... — auto-started")

                            # Annotate events with new_conversation_id if continuation was detected
                            if stream.new_conversation_id:
                                edata["new_conversation_id"] = stream.new_conversation_id

                            if etype in ("messages", "done"):
                                stream.status = edata.get("status", stream.status)
                                stream.provider = edata.get("provider", stream.provider)
                                if edata.get("messages"):
                                    stream.latest_frontend_messages = edata["messages"]

                                # --- File tracking for diff endpoint ---
                                raw_msgs = edata.get("raw_messages", [])
                                if raw_msgs:
                                    try:
                                        _track_file_changes(cid, raw_msgs)
                                    except Exception:
                                        pass

                                # --- Incremental persistence ---
                                # Save whenever the raw message list has grown,
                                # which happens after each tool execution round.
                                if raw_msgs and len(raw_msgs) > last_saved_msg_count:
                                    last_saved_msg_count = len(raw_msgs)
                                    try:
                                        store.save_messages(cid, raw_msgs)
                                        if edata.get("messages"):
                                            store.save_frontend_messages(cid, edata["messages"])
                                        # Keep the on-disk status in sync with the
                                        # live stream so that a page refresh during
                                        # a running continuation doesn't show a stale
                                        # "max_iterations_reached" / Continue button.
                                        # Don't overwrite "continued" — it was set
                                        # intentionally by the continuation detection
                                        # and the stream will finish soon anyway.
                                        if stream.status != "continued":
                                            store.update_status(cid, stream.status)
                                    except Exception as exc:
                                        logger.warning(
                                            f"[proxy] Incremental save failed for "
                                            f"{cid[:8]}... ({len(raw_msgs)} msgs): {exc}"
                                        )

                            for q in list(stream.subscribers):
                                try:
                                    q.put_nowait((etype, edata))
                                except asyncio.QueueFull:
                                    pass

                            # Notify parent stream about child activity
                            if stream.parent_id:
                                parent = active_streams.get(stream.parent_id)
                                if parent and not parent.finished:
                                    child_evt = ("subagent_event", {
                                        "child_id": cid,
                                        "tool_call_id": stream.tool_call_id,
                                        "event_type": etype,
                                        "status": stream.status,
                                    })
                                    for q in list(parent.subscribers):
                                        try:
                                            q.put_nowait(child_evt)
                                        except asyncio.QueueFull:
                                            pass

    except httpx.ConnectError:
        err = {"message": f"Cannot connect to backend at {BACKEND_URL}", "type": "ConnectionError"}
        stream.latest_event_type = "error"
        stream.latest_event_data = err
        for q in list(stream.subscribers):
            try:
                q.put_nowait(("error", err))
            except asyncio.QueueFull:
                pass

    except asyncio.CancelledError:
        logger.info(f"[proxy] Cancelled {cid[:8]}...")

    except Exception as e:
        logger.exception(f"[proxy] Error for {cid[:8]}...")
        err = {"message": str(e), "type": type(e).__name__}
        stream.latest_event_type = "error"
        stream.latest_event_data = err
        for q in list(stream.subscribers):
            try:
                q.put_nowait(("error", err))
            except asyncio.QueueFull:
                pass

    finally:
        stream.finished = True

        # Determine final status
        persist_status = stream.status
        if stream.cancel_event.is_set() and persist_status == "running":
            persist_status = "interrupted"
        elif persist_status == "running":
            persist_status = "error"

        # Final persistence pass — ensures the terminal status and any
        # last-moment messages are captured even if the incremental saves
        # already covered most of the data.
        raw_messages = []
        if stream.latest_event_data:
            raw_messages = stream.latest_event_data.get("raw_messages", [])

        # If the user cancelled mid-stream while tool calls were in flight,
        # inject synthetic tool responses so the persisted conversation stays
        # valid and can be resumed later.
        if stream.cancel_event.is_set() and raw_messages:
            raw_messages = _fix_orphan_tool_calls(raw_messages)

        try:
            # Don't overwrite "continued" status if the proxy already set it
            current_status = None
            try:
                conv = store.get_conversation(cid)
                current_status = conv.get("status")
            except Exception:
                pass
            if current_status != "continued":
                store.update_status(cid, persist_status)
            if raw_messages:
                store.save_messages(cid, raw_messages)
            if stream.latest_frontend_messages:
                store.save_frontend_messages(cid, stream.latest_frontend_messages)
        except Exception as e:
            logger.error(f"[proxy] Final persist failed for {cid[:8]}...: {e}")

        # Signal end to all subscribers
        for q in list(stream.subscribers):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

        # Remove from registry only if we're still the active stream
        async with _streams_lock:
            if active_streams.get(cid) is stream:
                del active_streams[cid]

        logger.info(f"[proxy] Ended {cid[:8]}... status={persist_status}")


# ============================================================================
# Subscriber SSE generator (shared by /api/chat and /stream)
# ============================================================================

_SSE_KEEPALIVE = ": keepalive\n\n"
_KEEPALIVE_INTERVAL = 8  # Send keepalive every 8 timeouts (≈16 seconds)


async def _subscriber_sse(
    stream: ActiveStream,
    queue: asyncio.Queue,
    request: Request,
    replay_latest: bool = False,
):
    """Yield SSE events from *queue*, cleaning up on disconnect.

    When *replay_latest* is True (mid-stream resume), the most recent
    state is synthesised as a ``messages`` event so the frontend can
    render the current progress immediately.  SSE keepalive comments
    are sent periodically to prevent proxy/browser connection timeouts.
    """
    try:
        # Replay: always send a proper "messages" event with the latest
        # frontend messages so the subscriber gets the current state,
        # regardless of what the actual last SSE event type was.
        if replay_latest:
            replay_data = None
            if stream.latest_event_data and stream.latest_event_type in ("messages", "done"):
                replay_data = stream.latest_event_data
            elif stream.latest_frontend_messages:
                raw = stream.latest_event_data.get("raw_messages", []) if stream.latest_event_data else []
                replay_data = {
                    "messages": stream.latest_frontend_messages,
                    "raw_messages": raw,
                    "status": stream.status,
                    "conversation_id": stream.conversation_id,
                    "provider": stream.provider,
                }
            if replay_data:
                yield _format_sse("messages", replay_data)

        if stream.finished:
            return

        idle_ticks = 0
        while True:
            try:
                if await request.is_disconnected():
                    break
                event = await asyncio.wait_for(queue.get(), timeout=2.0)
                if event is None:
                    break
                etype, edata = event
                yield _format_sse(etype, edata)
                idle_ticks = 0
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    break
                idle_ticks += 1
                if idle_ticks >= _KEEPALIVE_INTERVAL:
                    idle_ticks = 0
                    yield _SSE_KEEPALIVE
                continue
    finally:
        if queue in stream.subscribers:
            stream.subscribers.remove(queue)


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


# ============================================================================
# FastAPI Application
# ============================================================================

def _get_workspace() -> Optional[Path]:
    """Return the workspace directory (``/workspace`` in Docker, None otherwise)."""
    if WORKSPACE_DIR:
        p = Path(WORKSPACE_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return None


app = FastAPI(
    title="Conversation History",
    description="SSE proxy + conversation storage",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Endpoints — Streaming
# ============================================================================

@app.post("/api/chat")
async def proxy_chat(request: Request):
    """
    Proxy a chat request to the backend.

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
    """
    Attach to an in-progress stream (mid-stream resume).

    If the stream is still active, the latest state is sent immediately
    followed by all future events.  If it already finished, a single
    ``done`` event with the final state is returned.  Returns 404 if
    the conversation doesn't exist at all.
    """
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
# Endpoints — CRUD (storage)
@app.get("/api/providers")
async def list_providers():
    """Return available model providers (built-in + custom)."""
    providers = get_available_providers()
    for cp in get_custom_providers():
        if not any(p["id"] == cp["id"] for p in providers):
            providers.append({
                "id": cp["id"],
                "name": cp.get("name", cp["id"]),
                "description": cp.get("description", "Custom provider"),
                "supports_thinking": cp.get("supports_thinking", False),
                "custom": True,
            })
    return {"providers": providers, "default": get_default_provider()}


# ── Settings ─────────────────────────────────────────────────────

class _SettingsUpdate(BaseModel):
    """Partial update for settings."""
    api_keys: Optional[dict] = None
    provider_overrides: Optional[dict] = None
    custom_providers: Optional[list] = None
    other: Optional[dict] = None


@app.get("/api/settings")
async def get_settings():
    """Return current user settings."""
    settings = get_all_settings()
    settings.setdefault("api_keys", {})
    settings.setdefault("provider_overrides", {})
    settings.setdefault("custom_providers", get_custom_providers())
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
    provider_manager.reload()
    return result


# ── Workspace info ───────────────────────────────────────────────

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

class DeleteRequest(BaseModel):
    """Request body for deleting a file or folder."""
    path: str = Field(..., description="Relative path within the workspace")


@app.post("/api/workspace/upload")
async def upload_workspace(
    archive: UploadFile = File(...),
    project_name: str = Form("project"),
):
    """Upload a zip archive of a project into the agent workspace.

    The frontend compresses the selected folder into a single zip (respecting
    .gitignore, including .git/) and sends it here.  Files are extracted into
    ``<workspace>/<project_name>/``, so multiple repos can coexist.
    """
    work_dir = _get_workspace()
    if not work_dir:
        raise HTTPException(status_code=400, detail="No active workspace")

    # Sanitise the project folder name
    safe_name = Path(project_name).name  # strip any slashes / traversal
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


# ============================================================================
# Serve frontend static files (SPA – must be mounted after all API routes)
frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
else:
    logger.warning(
        "Frontend build not found at %s — rebuild the Docker image or run "
        "`cd frontend && npm run build` to serve the UI at /",
        frontend_dist,
    )


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="warning")
