"""
Conversation History Server — SSE proxy + conversation storage.

Sits between the frontend and the agent backend as an independent gate:

    Frontend  ←SSE→  Conversation Server (8081)  ←SSE→  Backend (8080)
                              ↕
                      data/conversations/

Key behaviors:
  - Proxies POST /api/chat to the backend, capturing the SSE stream
  - Keeps the backend connection alive even when the frontend disconnects
  - Frontend can reconnect mid-stream via GET /api/conversations/{id}/stream
  - Persists conversations automatically when rounds complete
  - Serves conversation history via REST endpoints

Start with::

    uvicorn conversation_history.api:app --host 0.0.0.0 --port 8081
"""

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .conversation_store import ConversationStore

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
    provider: Optional[str] = None
    latest_event_type: Optional[str] = None
    latest_event_data: Optional[dict] = None
    status: str = "running"
    subscribers: list = field(default_factory=list)
    finished: bool = False
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None


active_streams: Dict[str, ActiveStream] = {}
_streams_lock = asyncio.Lock()


async def _cancel_active_stream(conversation_id: str) -> None:
    """Cancel any in-progress stream for a conversation."""
    async with _streams_lock:
        old = active_streams.get(conversation_id)
    if old and not old.finished:
        old.cancel_event.set()
        if old.task and not old.task.done():
            old.task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(old.task), timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass


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
# Backend Proxy — background task
# ============================================================================

async def _proxy_backend_stream(stream: ActiveStream, request_body: dict):
    """
    POST to the backend, read its SSE stream, broadcast to subscribers,
    and persist when the round ends.

    This task runs independently of any frontend connection — the backend
    stream stays alive even if all subscribers disconnect.
    """
    cid = stream.conversation_id

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(None)) as client:
            async with client.stream(
                "POST",
                f"{BACKEND_URL}/api/chat",
                json=request_body,
                headers={"Content-Type": "application/json"},
            ) as response:
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

                            if etype in ("messages", "done"):
                                stream.status = edata.get("status", stream.status)
                                stream.provider = edata.get("provider", stream.provider)

                            for q in list(stream.subscribers):
                                try:
                                    q.put_nowait((etype, edata))
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

        # Persist conversation
        raw_messages = []
        if stream.latest_event_data:
            raw_messages = stream.latest_event_data.get("raw_messages", [])

        if raw_messages:
            try:
                store.create_conversation(
                    conversation_id=cid,
                    provider_id=stream.provider,
                    conv_type="user_chat",
                )
                store.save_messages(cid, raw_messages)
                store.update_status(cid, persist_status)
            except Exception as e:
                logger.error(f"[proxy] Persist failed for {cid[:8]}...: {e}")

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

async def _subscriber_sse(
    stream: ActiveStream,
    queue: asyncio.Queue,
    request: Request,
    replay_latest: bool = False,
):
    """Yield SSE events from *queue*, cleaning up on disconnect."""
    try:
        if replay_latest and stream.latest_event_data:
            yield _format_sse(stream.latest_event_type, stream.latest_event_data)

        if stream.finished:
            return

        while True:
            try:
                if await request.is_disconnected():
                    break
                event = await asyncio.wait_for(queue.get(), timeout=2.0)
                if event is None:
                    break
                etype, edata = event
                yield _format_sse(etype, edata)
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    break
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
    body = await request.json()
    conversation_id = body.get("conversation_id") or str(uuid.uuid4())
    body["conversation_id"] = conversation_id

    await _cancel_active_stream(conversation_id)

    stream = ActiveStream(
        conversation_id=conversation_id,
        provider=body.get("provider"),
    )

    async with _streams_lock:
        active_streams[conversation_id] = stream

    stream.task = asyncio.create_task(_proxy_backend_stream(stream, body))

    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    stream.subscribers.append(queue)

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

    async def _replay():
        yield _format_sse("done", {
            "conversation_id": conversation_id,
            "status": conv.get("status", "completed"),
            "messages": conv.get("messages", []),
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
            {"conversation_id": s.conversation_id, "status": s.status}
            for s in active_streams.values()
            if not s.finished
        ]
    }


# ============================================================================
# Endpoints — CRUD (storage)
# ============================================================================

@app.get("/")
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
    """Return full conversation: metadata + messages."""
    try:
        conv = store.get_conversation(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Conversation not found")
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
# Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="info")
