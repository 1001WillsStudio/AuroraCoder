"""
Streaming infrastructure for the Conversation Gateway.

Active stream management, SSE formatting, continuation detection, backend proxy,
and subscriber SSE — all the plumbing that powers the SSE-based chat streaming.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from src.config import WORKSPACE_DIR
from gateway.conversation_store import store
from gateway.workspace import (
    file_snapshots,
    mark_file_touched,
    snapshot_file,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8080")


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
    last_full_snapshot: Optional[dict] = None   # latest tier 3 snapshot for reconnection
    pending_deltas: list = field(default_factory=list)  # deltas since last full snapshot


active_streams: Dict[str, ActiveStream] = {}
_streams_lock = asyncio.Lock()


_FILE_WRITE_TOOLS = {"write_file", "edit_file", "delete_file"}

# Tool-call IDs for which we've already taken a pre-write snapshot.
# Prevents re-snapshotting after the write has already happened.
_snapshotted_tool_calls: Dict[str, set] = {}  # {conversation_id: {tool_call_id, ...}}


def _collect_orphan_tool_calls(raw_messages: list) -> list[dict]:
    """Return every assistant ``tool_call`` that has no matching ``tool``
    response.  Used by both the raw-message fixer and the frontend-message
    annotator so they stay in sync."""
    responded_ids: set = set()
    for msg in raw_messages:
        if msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id")
            if tc_id:
                responded_ids.add(tc_id)

    orphans: list[dict] = []
    for msg in raw_messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            tc_id = tc.get("id")
            if tc_id and tc_id not in responded_ids:
                orphans.append(tc)
    return orphans


def _fix_orphan_tool_calls(raw_messages: list) -> list:
    """
    Scan *raw_messages* for assistant ``tool_calls`` that have no matching
    ``tool`` response and inject a synthetic "stopped by user" result.
    """
    orphans = _collect_orphan_tool_calls(raw_messages)
    if not orphans:
        return list(raw_messages)

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
            file_path = args.get("file") or args.get("target_file") or args.get("file_path") or ""
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

    GUARANTEE: any stream that survives cancellation (task doesn't finish within
    the 2 s timeout) is force-removed from ``active_streams``.  No stream ever
    outlives a cancel call — a zombie stream would permanently block new
    conversations via the ``/api/conversations/active`` check.
    """
    t_start = time.perf_counter()
    tasks_to_await: List[asyncio.Task] = []
    streams_to_kill: List[ActiveStream] = []  # force-removed on timeout

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
        streams_to_kill.append(child)

    # Cancel the parent stream
    if old and not old.finished:
        old.cancel_event.set()
        if old.task and not old.task.done():
            old.task.cancel()
            tasks_to_await.append(old.task)
        streams_to_kill.append(old)

    # Cancel any continuation child created by this stream.
    # Also cascade to grandchildren (subagents spawned by the continuation).
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
                streams_to_kill.append(continuation)

                # Recursively cancel subagents that were spawned by the continuation
                grandchild_children = [
                    s for s in active_streams.values()
                    if s.parent_id == continuation.conversation_id and not s.finished
                ]
                for gc in grandchild_children:
                    logger.info(f"[cancel] Cascading cancel to grandchild {gc.conversation_id[:8]}...")
                    gc.cancel_event.set()
                    if gc.task and not gc.task.done():
                        gc.task.cancel()
                        tasks_to_await.append(gc.task)
                    streams_to_kill.append(gc)

    # Await all cancelled tasks concurrently — total wait bounded to 2 s
    if tasks_to_await:
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks_to_await, return_exceptions=True),
                timeout=2.0,
            )
        except (asyncio.TimeoutError, Exception):
            pass

    # GUARANTEE: force-remove any stream that survived the timeout.
    # The orphaned task will finish on its own eventually; its finally
    # block will see a different stream (or nothing) on the identity check
    # and exit cleanly.  Without this, a stuck task creates a permanent
    # zombie that blocks all new conversations.
    async with _streams_lock:
        for s in streams_to_kill:
            if not s.finished and active_streams.get(s.conversation_id) is s:
                s.finished = True
                del active_streams[s.conversation_id]
                logger.warning(
                    f"[cancel] Force-removed zombie stream "
                    f"{s.conversation_id[:8]}... after timeout"
                )

    elapsed = time.perf_counter() - t_start
    if elapsed > 0.1:
        logger.info(f"[cancel] [{conversation_id[:8]}...] duration={elapsed:.3f}s tasks_awaited={len(tasks_to_await)}")




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
    Scan assistant messages for the LAST (most recent) continue_as_new_chat
    tool call.  Returns its parsed arguments dict, or None if the most recent
    call is missing or has invalid JSON.  Earlier calls are never used.
    """
    for msg in reversed(raw_messages):
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            if tc.get("function", {}).get("name") == "continue_as_new_chat":
                try:
                    return json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    logger.warning(
                        "[continuation] Most recent continue_as_new_chat (id=%s) "
                        "has invalid JSON — aborting continuation.  Arguments: %s",
                        tc.get("id", "?"),
                        tc.get("function", {}).get("arguments", "")[:200],
                    )
                    return None
    return None


async def _start_continuation(new_cid: str, provider_id: str, user_msg: str):
    """
    Simulate a user opening a new chat, typing the continuation message,
    and pressing Send.  POSTs to the backend via the normal proxy flow.

    The continuation is a standalone main chat (conv_type="user_chat",
    parent_id=None) so the frontend displays it at the same level as the
    original chat — NOT as a subagent child in the sidebar.
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

                                    # Continuation is a new standalone main chat —
                                    # no parent_id, so the frontend does NOT display
                                    # it as a subagent child in the sidebar.
                                    store.create_conversation(
                                        conversation_id=new_cid,
                                        parent_id=None,
                                        conv_type="user_chat",
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

                            # ── Track full snapshots and deltas for reconnection ──
                            if etype == "messages":
                                stream.last_full_snapshot = edata
                                stream.pending_deltas.clear()
                            elif etype == "delta":
                                stream.pending_deltas.append(edata)

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

        # Orphan tool calls are NOT fixed here — they are healed lazily
        # the next time POST /api/chat is called (see proxy_chat in routes.py).

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
    request,
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
                if stream.finished:
                    break
                idle_ticks += 1
                if idle_ticks >= _KEEPALIVE_INTERVAL:
                    idle_ticks = 0
                    yield _SSE_KEEPALIVE
                continue
    finally:
        if queue in stream.subscribers:
            stream.subscribers.remove(queue)
