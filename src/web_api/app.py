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

from gateway.workspace import snapshot_file, mark_file_touched, clear_conversation_snapshots

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
    conversation_id: Optional[str] = Field(None)
    messages: Optional[list] = Field(None)
    provider: Optional[str] = Field(None)
    tools: Optional[str] = Field(None)


# ============================================================================
# Active stream tracking (for cancelling on new-send)
# ============================================================================

active_streams: Dict[str, threading.Event] = {}
active_streams_lock = threading.Lock()


def cancel_active_stream(conversation_id: str) -> bool:
    with active_streams_lock:
        if conversation_id in active_streams:
            active_streams[conversation_id].set()
            del active_streams[conversation_id]
            return True
    return False


def register_stream(conversation_id: str, cancel_event: threading.Event):
    with active_streams_lock:
        if conversation_id in active_streams:
            active_streams[conversation_id].set()
        active_streams[conversation_id] = cancel_event


def unregister_stream(conversation_id: str, cancel_event: threading.Event):
    with active_streams_lock:
        if active_streams.get(conversation_id) is cancel_event:
            del active_streams[conversation_id]


# ============================================================================
# Helpers
# ============================================================================

def get_filtered_tools(mode: str):
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
            frontend_messages.append({"role": "user", "content": msg.get("content", "")})
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
    max_iterations: int = 30, provider: Optional[str] = None,
    tools_override: Optional[list] = None, restart_shell: bool = False,
) -> AsyncGenerator[str, None]:
    cancel_event = threading.Event()
    cancel_active_stream(conversation_id)
    register_stream(conversation_id, cancel_event)

    try:
        queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def run_generator():
            try:
                if restart_shell:
                    try:
                        shell.restart()
                    except Exception as e:
                        logger.warning(f"Failed to restart shell: {e}")

                current_messages = messages
                status = "running"
                current_provider = provider

                for response in generate_chat_responses_stream_native(
                    messages=messages, max_iterations=max_iterations,
                    provider_id=provider, tools_override=tools_override,
                ):
                    if cancel_event.is_set():
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
                            "provider": current_provider,
                        })
                    )

                if not cancel_event.is_set():
                    final_messages = convert_messages_for_frontend(current_messages)
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ("done", {
                            "conversation_id": conversation_id,
                            "status": status,
                            "messages": final_messages,
                            "raw_messages": current_messages,
                            "provider": current_provider,
                        })
                    )
                else:
                    cancel_active_subagents(conversation_id)
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
        cancel_event.set()
        unregister_stream(conversation_id, cancel_event)


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
        set_file_tracking_callbacks(on_read=snapshot_file, on_write=mark_file_touched)
        logger.info("File tracking callbacks registered")
    except Exception as e:
        logger.error(f"Failed to initialize sandbox: {e}")
    yield
    logger.info("Shutting down...")
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


@app.post("/api/chat")
async def chat(chat_request: ChatRequest, request: Request):
    conversation_id = chat_request.conversation_id or str(uuid.uuid4())
    set_current_conversation(conversation_id)
    is_new = not chat_request.conversation_id
    if is_new:
        clear_conversation_snapshots(conversation_id)

    messages = (chat_request.messages or []).copy()
    if chat_request.message:
        messages.append({"role": "user", "content": chat_request.message})

    provider = chat_request.provider or DEFAULT_PROVIDER
    tools_override = get_filtered_tools(chat_request.tools) if chat_request.tools else None

    return StreamingResponse(
        stream_chat_response(messages, conversation_id, request, provider=provider, tools_override=tools_override, restart_shell=is_new),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache", "Connection": "keep-alive",
            "X-Accel-Buffering": "no", "X-Conversation-ID": conversation_id, "X-Provider": provider,
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
