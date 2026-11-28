"""
FastAPI Backend for ThinkWithTool AI Assistant

Provides the core agent SSE streaming endpoint.
All other endpoints (settings, providers, workspace, file display, conversations)
have been moved to ``gateway/api.py`` (port 8081, internal).
"""

import json
import uuid
import asyncio
import logging
import secrets
import threading
from datetime import datetime
from typing import Optional, Dict, Any, AsyncGenerator, List
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor


from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ..main_flow import generate_chat_responses_stream_native
from ..backend_access import backend_access_allowed
from ..code_sandbox import shell, get_workspace, WORKSPACE
from ..code_sandbox.terminal_sessions import stop_terminal_sessions
from ..core_tools.subagent import cancel_active_subagents
from ..config import DEFAULT_PROVIDER
from ..providers import provider_manager
from ..tool_definitions import (
    NATIVE_TOOL_DEFINITIONS, SUBAGENT_READ_ONLY_TOOLS, GAP_INVESTIGATION_TOOLS,
    MEMORY_MAINTENANCE_TOOLS, memory_filter_tools,
)

logger = logging.getLogger(__name__)

executor = ThreadPoolExecutor()


# ============================================================================
# Pydantic Models
# ============================================================================

class ChatMessage(BaseModel):
    role: str = Field(..., description="Message role")
    content: Optional[str] = Field(None)
    thinking: Optional[str] = Field(None)
    tool_calls: Optional[list] = Field(None)
    tool_call_id: Optional[str] = Field(None)


class ChatRequest(BaseModel):
    message: Optional[str] = Field(None)
    message_source: Optional[str] = Field(None)
    message_receipt: Optional[str] = Field(None)
    conversation_id: Optional[str] = Field(None)
    messages: Optional[list] = Field(None)
    provider: Optional[str] = Field(None)
    provider_id: Optional[str] = Field(None)
    model: Optional[str] = Field(None)
    tools: Optional[str] = Field(None)
    max_iterations: Optional[int] = Field(None)
    # Injected by the gateway before proxying — gives the agent a
    # pre-built workspace tree so it doesn't waste turns exploring.
    workspace_tree: Optional[str] = Field(None)
    conversation_history_token: Optional[str] = Field(None)
    task_state: Optional[dict] = Field(None)
    task_token: Optional[str] = Field(None)
    accept_turn_inputs: bool = Field(False)
    compact_delta_history: bool = Field(False)
    stream_stop_token: str | None = None


# ============================================================================
# Active stream tracking (for cancelling on new-send)
# ============================================================================

@dataclass
class ChatStream:
    """One backend stream and its per-turn cancellation credential."""

    stop_token: str | None
    cancel_event: threading.Event = field(default_factory=threading.Event)


active_streams: Dict[str, ChatStream] = {}
active_streams_lock = threading.Lock()


def cancel_active_stream(conversation_id: str, *, stop_token: str | None = None) -> bool:
    with active_streams_lock:
        stream = active_streams.get(conversation_id)
        if stream is None:
            return False
        if stop_token is not None and not secrets.compare_digest(
            (stream.stop_token or "").encode(), stop_token.encode(),
        ):
            return False
        stream.cancel_event.set()
        return True


def register_stream(conversation_id: str, stream: ChatStream):
    with active_streams_lock:
        previous = active_streams.get(conversation_id)
        if previous is not None:
            previous.cancel_event.set()
        active_streams[conversation_id] = stream


def unregister_stream(conversation_id: str, stream: ChatStream):
    with active_streams_lock:
        if active_streams.get(conversation_id) is stream:
            del active_streams[conversation_id]


# ============================================================================
# Helpers
# ============================================================================

def get_filtered_tools(mode: str):
    defs = []
    for td in NATIVE_TOOL_DEFINITIONS:
        name = td["function"]["name"]
        if name == "subagent":
            continue
        if mode == "read_only" and name not in SUBAGENT_READ_ONLY_TOOLS:
            continue
        # Gap investigation and memory maintenance share the same unified tool
        # set — see tool_definitions.py (GAP_INVESTIGATION_TOOLS ≡ MEMORY_MAINTENANCE_TOOLS).
        if mode in ("gap_investigation", "memory_maintenance") and name not in MEMORY_MAINTENANCE_TOOLS:
            continue
        defs.append(td)
    if mode in ("gap_investigation", "memory_maintenance"):
        return defs  # skip master-switch filter for worker modes
    return memory_filter_tools(defs)


def convert_messages_for_frontend(messages: list) -> list:
    frontend_messages = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role")

        if role == "system":
            i += 1
            continue
        elif role == "user":
            visible = {"role": "user", "content": msg.get("content", "")}
            if "source" in msg:
                visible["source"] = msg["source"]
            frontend_messages.append(visible)
            i += 1
        elif role == "assistant":
            activities = []
            thinking = msg.get("thinking") or msg.get("reasoning_content")
            if thinking:
                activities.append({"type": "thinking", "content": thinking})
            for tc in msg.get("tool_calls", []):
                tc_func = tc.get("function", {})
                activities.append({
                    "type": "tool_call", "id": tc.get("id", ""),
                    "name": tc_func.get("name", ""), "arguments": tc_func.get("arguments", "{}"),
                })
            j = i + 1
            while j < len(messages) and messages[j].get("role") == "tool":
                tool_msg = messages[j]
                content = tool_msg.get("content", "")
                if len(content) > 3000:
                    content = content[:3000] + "\n... [truncated]"
                activities.append({
                    "type": "tool_result",
                    "tool_call_id": tool_msg.get("tool_call_id", ""),
                    "content": content,
                })
                j += 1
            frontend_messages.append({
                "role": "assistant",
                "content": msg.get("content", ""),
                "activities": activities,
            })
            i = j
        elif role == "tool":
            i += 1
        else:
            i += 1
    return frontend_messages


def format_sse_event(event_type: str, data: Any) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"






async def stream_chat_response(
    messages: list, conversation_id: str, request: Request,
    max_iterations: int, provider: Optional[str] = None,
    model: Optional[str] = None,
    tools_override: Optional[list] = None,
    workspace_tree: str = "",
    conversation_history_token: Optional[str] = None,
    task_state: Optional[dict] = None,
    task_token: Optional[str] = None,
    accept_turn_inputs: bool = False,
    compact_delta_history: bool = False,
    stream_stop_token: str | None = None,
) -> AsyncGenerator[str, None]:
    stream = ChatStream(stream_stop_token)
    cancel_event = stream.cancel_event
    register_stream(conversation_id, stream)

    seq = 0

    try:
        queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def run_generator():
            nonlocal seq
            try:
                current_messages = messages
                status = "running"
                current_provider = provider
                snapshot_prefix = None
                snapshot_seq = None

                task_options = {}
                if task_token:
                    from src.task_checklist import AgentTaskTools

                    task_options["task_tools"] = AgentTaskTools(
                        conversation_id, task_token, task_state, accept_turn_inputs=accept_turn_inputs,
                    )
                for response in generate_chat_responses_stream_native(
                    messages=messages, max_iterations=max_iterations,
                    provider_id=provider, tools_override=tools_override,
                    model=model,
                    conversation_id=conversation_id,
                    workspace_tree=workspace_tree,
                    **({"turn_token": conversation_history_token} if conversation_history_token else {}),
                    stop_requested=cancel_event.is_set,
                    **task_options,
                ):
                    if cancel_event.is_set():
                        if response["status"] == "interrupted":
                            current_messages = response["messages"]
                        break
                    seq += 1
                    current_messages = response["messages"]
                    status = response["status"]
                    current_provider = response.get("provider", provider)

                    llm_delta = response.get("llm_delta")
                    frontend_messages = convert_messages_for_frontend(current_messages)

                    common = {
                        "seq": seq,
                        "messages": frontend_messages,
                        "status": status,
                        "conversation_id": conversation_id,
                        "provider": current_provider,
                    }
                    prefix = current_messages[:-1]
                    if (compact_delta_history and llm_delta is not None and current_messages
                            and prefix == snapshot_prefix and status == "running"):
                        common["raw_delta"] = {
                            "snapshot_seq": snapshot_seq,
                            "message": deepcopy(current_messages[-1]),
                        }
                    else:
                        common["raw_messages"] = current_messages
                        snapshot_prefix = prefix
                        snapshot_seq = seq

                    if status == "error":
                        common["message"] = response["error"]
                        common["type"] = "ModelStreamError"
                        loop.call_soon_threadsafe(queue.put_nowait, ("error", common))
                        return
                    if llm_delta is not None:
                        common["delta"] = llm_delta
                        loop.call_soon_threadsafe(queue.put_nowait, ("delta", common))
                    else:
                        loop.call_soon_threadsafe(queue.put_nowait, ("messages", common))

                if cancel_event.is_set():
                    if stream_stop_token is None:
                        return
                    status = "interrupted"
                    current_messages = deepcopy(current_messages)
                # Both terminal events carry a full snapshot, including after Stop.
                final = {
                    "seq": seq + 1,
                    "messages": convert_messages_for_frontend(current_messages),
                    "raw_messages": current_messages,
                    "status": status,
                    "conversation_id": conversation_id,
                    "provider": current_provider,
                }
                loop.call_soon_threadsafe(queue.put_nowait, ("messages", final))
                loop.call_soon_threadsafe(queue.put_nowait, ("done", {**final, "seq": seq + 2}))
            except Exception as e:
                if not cancel_event.is_set():
                    logger.exception("Error in generator thread")
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ("error", {"message": str(e), "type": type(e).__name__})
                    )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        executor.submit(run_generator)

        while True:
            try:
                if await request.is_disconnected():
                    cancel_event.set()
                    break
                event = await asyncio.wait_for(queue.get(), timeout=2.0)
                if event is None:
                    break
                event_type, event_data = event
                yield format_sse_event(event_type, event_data)
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    cancel_event.set()
                    break
                continue
    finally:
        was_cancelled = cancel_event.is_set()
        cancel_event.set()
        unregister_stream(conversation_id, stream)
        # Cancel any active subagents that were spawned by this conversation.
        # This runs at the asyncio level (not the generator thread), so it
        # always executes — even when the generator is stuck inside
        # run_subagent() waiting on an HTTP response.
        if was_cancelled:
            cancel_active_subagents(conversation_id)


# ============================================================================
# Application
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing sandbox...")
    try:
        workspace = get_workspace()
        logger.info(f"Workspace: {workspace}")
        shell.start()
        provider_manager.reload()
        logger.info("Provider clients loaded from settings.json")
    except Exception as e:
        logger.error(f"Failed to initialize sandbox: {e}")
    yield
    logger.info("Shutting down...")
    stop_terminal_sessions()
    shell.stop()
    executor.shutdown(wait=False)


app = FastAPI(title="AuroraCoder API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/")
async def root():
    return {"status": "ok", "service": "AuroraCoder", "version": "1.0.0"}


@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "workspace": str(WORKSPACE),
        "shell_alive": shell.is_alive,
    }

def require_backend_access(request: Request) -> None:
    if not backend_access_allowed(request.headers):
        raise HTTPException(status_code=401, detail="Backend authentication required")


@app.post("/api/reload", dependencies=[Depends(require_backend_access)])
async def reload_providers():
    """Called by the gateway after settings change.  Re-reads settings.json
    from disk and rebuilds the in-memory client cache."""
    provider_manager.reload()
    return {"ok": True}


class ChatStopRequest(BaseModel):
    stop_token: str = Field(min_length=1)


@app.post(
    "/internal/chat/{conversation_id}/stop",
    dependencies=[Depends(require_backend_access)],
    status_code=204,
)
async def stop_chat(conversation_id: str, stop: ChatStopRequest):
    if not cancel_active_stream(conversation_id, stop_token=stop.stop_token):
        raise HTTPException(status_code=404, detail="Chat stream not found")
    return Response(status_code=204)


@app.post("/api/chat", dependencies=[Depends(require_backend_access)])
async def chat(chat_request: ChatRequest, request: Request):
    conversation_id = chat_request.conversation_id or str(uuid.uuid4())

    messages = (chat_request.messages or []).copy()
    if chat_request.message:
        message = {"role": "user", "content": chat_request.message}
        if chat_request.message_source:
            message["source"] = chat_request.message_source
        if chat_request.message_receipt:
            message["turn_input_receipt"] = chat_request.message_receipt
        messages.append(message)

    provider = chat_request.provider_id or chat_request.provider or DEFAULT_PROVIDER
    tools_override = get_filtered_tools(chat_request.tools) if chat_request.tools else None
    max_iterations = chat_request.max_iterations or 30

    workspace_tree = chat_request.workspace_tree or ""
    task_options = {}
    if chat_request.task_token:
        task_options = {
            "task_state": chat_request.task_state, "task_token": chat_request.task_token,
            "accept_turn_inputs": chat_request.accept_turn_inputs,
        }
    return StreamingResponse(
        stream_chat_response(messages, conversation_id, request, provider=provider,
                             model=chat_request.model, max_iterations=max_iterations,
                             tools_override=tools_override,
                             workspace_tree=workspace_tree,
                             conversation_history_token=chat_request.conversation_history_token,
                             compact_delta_history=chat_request.compact_delta_history,
                             stream_stop_token=chat_request.stream_stop_token, **task_options),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache", "Connection": "keep-alive",
            "X-Accel-Buffering": "no", "X-Conversation-ID": conversation_id, "X-Provider": provider,
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
