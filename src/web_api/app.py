"""
FastAPI Backend for ThinkWithTool AI Assistant

Provides REST API endpoints with SSE streaming for real-time chat responses.
File-display endpoints (diff, tree, read, workspace) have been moved to
``conversation_gateway/api.py`` (port 8081).
"""

import json
import uuid
import asyncio
import logging
import threading
from datetime import datetime
from typing import Optional, Dict, Any, AsyncGenerator, List
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ..main_flow import generate_chat_responses_stream_native
from ..providers import get_available_providers, get_default_provider
from ..code_sandbox import shell, get_workspace, WORKSPACE
from ..code_tools.file_operations import set_file_tracking_callbacks, set_current_conversation
from ..core_tools.subagent import cancel_active_subagents
from ..config import DEFAULT_PROVIDER

from conversation_gateway.workspace import snapshot_file, mark_file_touched, clear_conversation_snapshots

logger = logging.getLogger(__name__)

# Thread pool for running synchronous generators
executor = ThreadPoolExecutor(max_workers=4)

# ============================================================================
# Pydantic Models
# ============================================================================

class ChatMessage(BaseModel):
    """A single chat message."""
    role: str = Field(..., description="Message role: 'user', 'assistant', 'system', or 'tool'")
    content: Optional[str] = Field(None, description="Message content")
    thinking: Optional[str] = Field(None, description="Reasoning/thinking content")
    tool_calls: Optional[list] = Field(None, description="Tool calls made by assistant")
    tool_call_id: Optional[str] = Field(None, description="ID of the tool call this message responds to")


class ChatRequest(BaseModel):
    """Request body for chat endpoint."""
    message: Optional[str] = Field(None, description="User message text (omit for continue)")
    conversation_id: Optional[str] = Field(None, description="Conversation ID for stream management")
    messages: Optional[list] = Field(None, description="Full conversation history from frontend (required for continue/resume)")
    provider: Optional[str] = Field(None, description="Model provider to use (e.g., 'deepseek', 'nvidia')")
    tools: Optional[str] = Field(None, description="Tool mode: 'read_only' or 'all'")


# Track active streams per conversation - allows cancelling previous stream when new one starts
active_streams: Dict[str, threading.Event] = {}
active_streams_lock = threading.Lock()

def cancel_active_stream(conversation_id: str) -> bool:
    """Cancel any active stream for the given conversation. Returns True if a stream was cancelled."""
    with active_streams_lock:
        if conversation_id in active_streams:
            logger.info(f"[cancel_active_stream] Cancelling active stream for {conversation_id}")
            active_streams[conversation_id].set()
            del active_streams[conversation_id]
            return True
    return False


def register_stream(conversation_id: str, cancel_event: threading.Event):
    """Register a new active stream for the conversation."""
    with active_streams_lock:
        if conversation_id in active_streams:
            logger.info(f"[register_stream] Replacing existing stream for {conversation_id}")
            active_streams[conversation_id].set()
        active_streams[conversation_id] = cancel_event


def unregister_stream(conversation_id: str, cancel_event: threading.Event):
    """Unregister a stream (only if it's still the active one)."""
    with active_streams_lock:
        if active_streams.get(conversation_id) is cancel_event:
            del active_streams[conversation_id]


def get_filtered_tools(mode: str):
    """Return tool definitions filtered by mode."""
    from ..tool_definitions import NATIVE_TOOL_DEFINITIONS, SUBAGENT_READ_ONLY_TOOLS
    defs = []
    for td in NATIVE_TOOL_DEFINITIONS:
        name = td["function"]["name"]
        if name == "subagent":
            continue
        if mode == "read_only" and name not in SUBAGENT_READ_ONLY_TOOLS:
            continue
        defs.append(td)
    return defs


# ============================================================================
# Message Conversion for Frontend
# ============================================================================

def convert_messages_for_frontend(messages: list) -> list:
    """Convert backend message format to frontend-friendly format."""
    frontend_messages = []
    i = 0

    logger.debug(f"[convert] Processing {len(messages)} messages")

    while i < len(messages):
        msg = messages[i]
        role = msg.get("role")

        if role == "system":
            i += 1
            continue

        elif role == "user":
            frontend_messages.append({
                "role": "user",
                "content": msg.get("content", "")
            })
            i += 1

        elif role == "assistant":
            activities = []

            thinking = msg.get("thinking") or msg.get("reasoning_content")
            if thinking:
                activities.append({"type": "thinking", "content": thinking})

            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                tc_func = tc.get("function", {})
                activities.append({
                    "type": "tool_call",
                    "id": tc.get("id", ""),
                    "name": tc_func.get("name", ""),
                    "arguments": tc_func.get("arguments", "{}")
                })

            j = i + 1
            while j < len(messages) and messages[j].get("role") == "tool":
                tool_msg = messages[j]
                tool_content = tool_msg.get("content", "")
                if len(tool_content) > 3000:
                    tool_content = tool_content[:3000] + "\n... [truncated]"
                activities.append({
                    "type": "tool_result",
                    "tool_call_id": tool_msg.get("tool_call_id", ""),
                    "content": tool_content
                })
                j += 1

            content = msg.get("content", "")
            assistant_msg = {
                "role": "assistant",
                "content": content,
                "activities": activities
            }
            logger.info(f"[convert] Assistant message with {len(activities)} activities, content_len={len(content)}")
            frontend_messages.append(assistant_msg)
            i = j

        elif role == "tool":
            i += 1

        else:
            i += 1

    return frontend_messages


# ============================================================================
# SSE Event Formatting
# ============================================================================

def format_sse_event(event_type: str, data: Any) -> str:
    """Format data as a Server-Sent Event."""
    json_data = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {json_data}\n\n"


async def stream_chat_response(
    messages: list,
    conversation_id: str,
    request: Request,
    max_iterations: int = 30,
    provider: Optional[str] = None,
    tools_override: Optional[list] = None,
) -> AsyncGenerator[str, None]:
    """Stream chat responses as SSE events."""
    cancel_event = threading.Event()

    cancel_active_stream(conversation_id)
    register_stream(conversation_id, cancel_event)

    try:
        queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def run_generator():
            try:
                current_messages = messages
                status = "running"
                current_provider = provider

                for response in generate_chat_responses_stream_native(
                    messages=messages,
                    max_iterations=max_iterations,
                    provider_id=provider,
                    tools_override=tools_override,
                ):
                    if cancel_event.is_set():
                        logger.info(f"[stream] Client disconnected, stopping generation for {conversation_id}")
                        break

                    current_messages = response["messages"]
                    status = response["status"]
                    current_provider = response.get("provider", provider)

                    frontend_messages = convert_messages_for_frontend(current_messages)

                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ("messages", {
                            "messages": frontend_messages,
                            "raw_messages": current_messages,
                            "status": status,
                            "conversation_id": conversation_id,
                            "provider": current_provider
                        })
                    )

                if not cancel_event.is_set():
                    final_status = status
                    final_messages = convert_messages_for_frontend(current_messages)
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ("done", {
                            "conversation_id": conversation_id,
                            "status": final_status,
                            "messages": final_messages,
                            "raw_messages": current_messages,
                            "provider": current_provider
                        })
                    )
                else:
                    logger.info(f"[stream] Client disconnected for {conversation_id}")
                    cancel_active_subagents()

            except Exception as e:
                if not cancel_event.is_set():
                    logger.exception("Error in generator thread")
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ("error", {"message": str(e), "type": type(e).__name__})
                    )
            finally:
                cancel_active_subagents()
                loop.call_soon_threadsafe(queue.put_nowait, None)

        future = executor.submit(run_generator)

        while True:
            try:
                if await request.is_disconnected():
                    logger.info(f"[stream] Client disconnected, signaling cancellation for {conversation_id}")
                    cancel_event.set()
                    break

                event = await asyncio.wait_for(queue.get(), timeout=2.0)

                if event is None:
                    break

                event_type, event_data = event
                yield format_sse_event(event_type, event_data)

            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    logger.info(f"[stream] Client disconnected during timeout, signaling cancellation for {conversation_id}")
                    cancel_event.set()
                    break
                continue

    except Exception as e:
        logger.exception("Error in stream_chat_response")
        yield format_sse_event("error", {"message": str(e), "type": type(e).__name__})
    finally:
        cancel_event.set()
        unregister_stream(conversation_id, cancel_event)


# ============================================================================
# Application Lifecycle
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - handles startup and shutdown."""
    logger.info("Initializing sandbox...")
    try:
        workspace = get_workspace()
        logger.info(f"Workspace: {workspace}")

        shell.start()
        logger.info("Persistent shell started")

        set_file_tracking_callbacks(
            on_read=snapshot_file,
            on_write=mark_file_touched
        )
        logger.info("File tracking callbacks registered")

    except Exception as e:
        logger.error(f"Failed to initialize sandbox: {e}")

    yield

    logger.info("Shutting down...")
    shell.stop()
    executor.shutdown(wait=False)


# ============================================================================
# FastAPI Application
# ============================================================================

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="AuroraCoder API",
        description="Your intelligent coding companion - AI-powered code assistant",
        version="1.0.0",
        lifespan=lifespan
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    return app


app = create_app()


# ============================================================================
# API Endpoints
# ============================================================================

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


@app.get("/api/providers")
async def list_providers():
    providers = get_available_providers()
    return {"providers": providers, "default": get_default_provider()}


# ============================================================================
# Workspace Info
# ============================================================================

@app.get("/api/workspace")
async def get_workspace_info():
    """Return basic workspace info."""
    return {
        "workspace": str(WORKSPACE),
        "shell_alive": shell.is_alive,
    }


# ============================================================================
# Chat Endpoint
# ============================================================================

@app.post("/api/chat")
async def chat(chat_request: ChatRequest, request: Request):
    """Stateless chat endpoint. Returns a streaming response with SSE events."""
    conversation_id = chat_request.conversation_id or str(uuid.uuid4())
    logger.info(f"[API] /api/chat called with conversation_id: {chat_request.conversation_id}, new_id: {conversation_id}")

    set_current_conversation(conversation_id)

    is_continue = bool(chat_request.messages and not chat_request.message)

    if not is_continue:
        clear_conversation_snapshots(conversation_id)
        try:
            shell.restart()
            logger.info(f"[API] Restarted persistent shell for new conversation {conversation_id}")
        except Exception as e:
            logger.warning(f"[API] Failed to restart shell: {e}")

    messages = (chat_request.messages or []).copy()
    if chat_request.message:
        messages.append({"role": "user", "content": chat_request.message})

    logger.info(f"[API] Total messages: {len(messages)}, provider: {chat_request.provider}")

    provider = chat_request.provider or DEFAULT_PROVIDER

    tools_override = get_filtered_tools(chat_request.tools) if chat_request.tools else None
    if tools_override is not None:
        tool_names = [td["function"]["name"] for td in tools_override]
        logger.info(f"[API] Tool override ({chat_request.tools}): {tool_names}")

    return StreamingResponse(
        stream_chat_response(messages, conversation_id, request, provider=provider, tools_override=tools_override),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Conversation-ID": conversation_id,
            "X-Provider": provider
        }
    )


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
