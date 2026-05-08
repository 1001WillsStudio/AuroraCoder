"""
FastAPI Backend for ThinkWithTool AI Assistant

Provides REST API endpoints with SSE streaming for real-time chat responses.
"""

import json
import uuid
import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any, AsyncGenerator, List
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ..main_flow import generate_chat_responses_stream_native
from ..providers import get_available_providers, get_default_provider
from ..code_sandbox import init_application_session, get_session_status
from ..code_sandbox.session_utils import session_manager, load_session_environment, list_loadable_sessions
from ..code_tools.file_operations import set_file_tracking_callbacks, set_current_conversation
from ..core_tools.subagent import cancel_active_subagents
from ..config import DEFAULT_BASE_ENV_NAME, DEFAULT_PROVIDER, DOCKER_MODE, WORKSPACE_DIR
from pathlib import Path
import difflib
import shutil

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
# Maps conversation_id -> threading.Event (cancel signal)
import threading
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
        # Cancel any existing stream first
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
    """Return tool definitions filtered by mode, excluding the subagent tool."""
    from ..tool_definitions import NATIVE_TOOL_DEFINITIONS, READ_ONLY_TOOLS
    defs = []
    for td in NATIVE_TOOL_DEFINITIONS:
        name = td["function"]["name"]
        if name == "subagent":
            continue
        if mode == "read_only" and name not in READ_ONLY_TOOLS:
            continue
        defs.append(td)
    return defs



# ============================================================================
# Message Conversion for Frontend
# ============================================================================

def convert_messages_for_frontend(messages: list) -> list:
    """
    Convert backend message format to frontend-friendly format.
    
    Backend format:
    - system messages
    - user messages  
    - assistant messages with thinking, content, tool_calls
    - tool response messages
    
    Frontend format:
    - user messages with content
    - assistant messages with activities timeline
    """
    frontend_messages = []
    i = 0
    
    logger.debug(f"[convert] Processing {len(messages)} messages")
    
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role")
        
        if role == "system":
            # Skip system messages for frontend display
            i += 1
            continue
            
        elif role == "user":
            frontend_messages.append({
                "role": "user",
                "content": msg.get("content", "")
            })
            i += 1
            
        elif role == "assistant":
            # Build assistant message with activities timeline
            activities = []
            
            # Add thinking if present
            thinking = msg.get("thinking") or msg.get("reasoning_content")
            if thinking:
                activities.append({
                    "type": "thinking",
                    "content": thinking
                })
            
            # Add tool calls if present
            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                tc_func = tc.get("function", {})
                activities.append({
                    "type": "tool_call",
                    "id": tc.get("id", ""),
                    "name": tc_func.get("name", ""),
                    "arguments": tc_func.get("arguments", "{}")
                })
            
            # Collect tool results that follow this assistant message
            j = i + 1
            while j < len(messages) and messages[j].get("role") == "tool":
                tool_msg = messages[j]
                tool_content = tool_msg.get("content", "")
                # Truncate long results
                if len(tool_content) > 3000:
                    tool_content = tool_content[:3000] + "\n... [truncated]"
                activities.append({
                    "type": "tool_result",
                    "tool_call_id": tool_msg.get("tool_call_id", ""),
                    "content": tool_content
                })
                j += 1
            
            # Add content if present (final response)
            content = msg.get("content", "")
            
            assistant_msg = {
                "role": "assistant",
                "content": content,
                "activities": activities
            }
            logger.info(f"[convert] Assistant message with {len(activities)} activities, content_len={len(content)}")
            for act in activities:
                logger.debug(f"[convert]   - {act.get('type')}: {act.get('name', '')[:30] if act.get('name') else act.get('content', '')[:30]}")
            
            frontend_messages.append(assistant_msg)
            
            # Move past the tool messages we already processed
            i = j
            
        elif role == "tool":
            # Tool messages should be handled with their assistant message
            # If we get here, it's an orphan - skip it
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
    """
    Stream chat responses as SSE events.
    
    Yields SSE events with the following types:
    - 'thinking': Reasoning/thinking content updates
    - 'content': Text content updates
    - 'tool_call': Tool call information
    - 'tool_result': Tool execution results
    - 'done': Final response with status
    - 'error': Error information
    
    Monitors client connection and stops generation if client disconnects.
    Also cancels any previous active stream for the same conversation.
    """
    cancel_event = threading.Event()
    
    # Cancel any existing stream for this conversation and register this one
    cancel_active_stream(conversation_id)
    register_stream(conversation_id, cancel_event)
    
    try:
        # Use asyncio queue to communicate between threads
        queue = asyncio.Queue()
        loop = asyncio.get_event_loop()
        
        def run_generator():
            """Run the synchronous generator in a thread."""
            try:
                last_sent_hash = None
                current_messages = messages
                status = "running"
                current_provider = provider
                
                for response in generate_chat_responses_stream_native(
                    messages=messages,
                    max_iterations=max_iterations,
                    provider_id=provider,
                    tools_override=tools_override,
                ):
                    # Check if client has disconnected
                    if cancel_event.is_set():
                        logger.info(f"[stream] Client disconnected, stopping generation for {conversation_id}")
                        break
                    
                    current_messages = response["messages"]
                    status = response["status"]
                    current_provider = response.get("provider", provider)
                    
                    # Convert messages to a frontend-friendly format
                    frontend_messages = convert_messages_for_frontend(current_messages)
                    
                    # Only send if changed (simple hash check)
                    msg_hash = hash(json.dumps(frontend_messages, default=str))
                    if msg_hash != last_sent_hash:
                        last_sent_hash = msg_hash
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            ("messages", {
                                "messages": frontend_messages, 
                                "raw_messages": current_messages,  # Include backend format for interrupt/resume
                                "status": status,
                                "conversation_id": conversation_id,  # Include for early tracking
                                "provider": current_provider
                            })
                        )
                
                # Only send done event if not cancelled
                if not cancel_event.is_set():
                    # Send completion event
                    final_status = status
                    final_messages = convert_messages_for_frontend(current_messages)
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ("done", {
                            "conversation_id": conversation_id,
                            "status": final_status,
                            "messages": final_messages,
                            "raw_messages": current_messages,  # Include backend format for interrupt/resume
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
                # Cancel any running subagents before finishing
                cancel_active_subagents()
                # Signal completion
                loop.call_soon_threadsafe(queue.put_nowait, None)
        
        # Start the generator in a thread
        future = executor.submit(run_generator)
        
        # Yield events from the queue, checking for client disconnect
        while True:
            try:
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.info(f"[stream] Client disconnected, signaling cancellation for {conversation_id}")
                    cancel_event.set()
                    break
                
                # Wait for events with short timeout to allow disconnect checks
                event = await asyncio.wait_for(queue.get(), timeout=2.0)
                
                if event is None:
                    # Generator finished
                    break
                
                event_type, event_data = event
                yield format_sse_event(event_type, event_data)
                
            except asyncio.TimeoutError:
                # Check disconnect more frequently, send keepalive periodically
                if await request.is_disconnected():
                    logger.info(f"[stream] Client disconnected during timeout, signaling cancellation for {conversation_id}")
                    cancel_event.set()
                    break
                # Send keepalive every ~60 seconds (30 iterations)
                continue
        
    except Exception as e:
        logger.exception("Error in stream_chat_response")
        yield format_sse_event("error", {
            "message": str(e),
            "type": type(e).__name__
        })
    finally:
        # Ensure cancel is signaled on any exit and unregister the stream
        cancel_event.set()
        unregister_stream(conversation_id, cancel_event)


# ============================================================================
# Application Lifecycle
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - handles startup and shutdown."""
    # Startup
    logger.info("Initializing session environment...")
    try:
        if DOCKER_MODE and WORKSPACE_DIR:
            workspace = Path(WORKSPACE_DIR)
            workspace.mkdir(parents=True, exist_ok=True)
            session_manager.working_directory_override = workspace
            logger.info(f"Docker mode: workspace override set to {workspace}")

        session_info = init_application_session(
            app_name="web_api_assistant",
            cleanup_on_exit=False,
            max_old_sessions=10,
            base_env_name=DEFAULT_BASE_ENV_NAME,
            reuse_env=DOCKER_MODE
        )
        
        if session_info['status'] == 'failed':
            logger.error(f"Session creation failed: {session_info.get('error', 'Unknown error')}")
        else:
            logger.info(f"Session initialized: {session_info['session_dir']}")
        
        # Register file tracking callbacks for diff support
        set_file_tracking_callbacks(
            on_read=snapshot_file,
            on_write=mark_file_touched
        )
        logger.info("File tracking callbacks registered")
            
    except Exception as e:
        logger.error(f"Failed to initialize session: {e}")
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
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
    
    # CORS middleware for frontend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, specify actual origins
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
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "AuroraCoder",
        "version": "1.0.0"
    }


@app.get("/api/health")
async def health_check():
    """Detailed health check."""
    try:
        session_status = get_session_status()
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "session": session_status
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)}
        )


@app.get("/api/providers")
async def list_providers():
    """
    List available model providers.
    
    Returns a list of providers that can be used for chat.
    Each provider has: id, name, description, supports_thinking
    """
    providers = get_available_providers()
    return {
        "providers": providers,
        "default": get_default_provider()
    }


# ============================================================================
# Session Management Endpoints
# ============================================================================

class LoadSessionRequest(BaseModel):
    """Request body for loading a session."""
    session_id: Optional[str] = Field(None, description="Session ID to load")
    session_name: Optional[str] = Field(None, description="Session name to load")


@app.get("/api/sessions")
async def list_sessions(loadable_only: bool = True):
    """
    List available sessions.
    
    Args:
        loadable_only: If true (default), only return sessions that can be loaded
                      (conda environment still exists). Set to false to see all sessions.
    
    Returns:
        List of sessions with their status and loadability
    """
    try:
        if loadable_only:
            result = list_loadable_sessions()
        else:
            sessions = session_manager.list_sessions(include_loadable_only=False)
            result = {
                "status": "success",
                "sessions": sessions,
                "total_sessions": len(sessions)
            }
        
        # Add current session info
        result["current_session"] = {
            "session_id": session_manager.session_id,
            "session_name": session_manager.session_info.get("session_name") if session_manager.session_info else None,
            "session_dir": str(session_manager.get_session_working_directory())
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to list sessions: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)}
        )


@app.post("/api/sessions/load")
async def load_session(request: LoadSessionRequest):
    """
    Load a previous session and continue working in it.
    
    This will:
    1. Verify the session exists and its conda environment is still available
    2. Switch to the session's working directory
    3. Activate the session's conda environment
    
    Note: This will terminate any current session's shell.
    """
    if not request.session_id and not request.session_name:
        raise HTTPException(
            status_code=400, 
            detail="Either session_id or session_name must be provided"
        )
    
    try:
        result = load_session_environment(
            session_id=request.session_id,
            session_name=request.session_name,
            auto_cleanup=False  # Don't auto-cleanup loaded sessions
        )
        
        if result.get("status") == "failed":
            raise HTTPException(status_code=400, detail=result.get("error", "Failed to load session"))
        
        return {
            "status": "success",
            "message": f"Session loaded successfully: {result.get('session_name')}",
            "session": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to load session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sessions/current")
async def get_current_session():
    """
    Get information about the currently active session.
    """
    if not session_manager.session_id:
        return {
            "status": "no_session",
            "message": "No active session"
        }
    
    return {
        "status": "active",
        "session_id": session_manager.session_id,
        "session_name": session_manager.session_info.get("session_name"),
        "session_dir": str(session_manager.get_session_working_directory()),
        "conda_env_name": session_manager.conda_env_name,
        "base_env_name": session_manager.base_env_name,
        "created_at": session_manager.session_info.get("created_at"),
        "loaded_at": session_manager.session_info.get("loaded_at")
    }


@app.post("/api/sessions/new")
async def create_new_session(session_name: Optional[str] = None, base_env_name: Optional[str] = None):
    """
    Create a new session (replacing the current one if any).
    
    Args:
        session_name: Optional name for the new session
        base_env_name: Optional conda environment to clone from
    """
    try:
        # Clean up current session's shell (but don't delete the env/files)
        if session_manager.persistent_shell:
            try:
                session_manager.persistent_shell.terminate()
                session_manager.persistent_shell.wait(timeout=5)
            except Exception:
                pass
            session_manager.persistent_shell = None
        
        # Create new session
        result = session_manager.create_session(
            session_name=session_name,
            base_env_name=base_env_name or DEFAULT_BASE_ENV_NAME
        )
        
        if result.get("status") == "failed":
            raise HTTPException(status_code=500, detail=result.get("error", "Failed to create session"))
        
        return {
            "status": "success",
            "message": f"New session created: {result.get('session_name')}",
            "session": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create new session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/chat")
async def chat(chat_request: ChatRequest, request: Request):
    """
    Stateless chat endpoint. Returns a streaming response with SSE events.
    
    The frontend owns all conversation state.  For a new turn, send `message`
    (and optionally prior `messages`).  To continue after max-iterations, send
    the `raw_messages` from the last SSE payload as `messages` without a `message`.
    """
    # Generate or use existing conversation ID
    conversation_id = chat_request.conversation_id or str(uuid.uuid4())
    
    logger.info(f"[API] /api/chat called with conversation_id: {chat_request.conversation_id}, new_id: {conversation_id}")
    
    set_current_conversation(conversation_id)
    
    is_continue = bool(chat_request.messages and not chat_request.message)
    
    # On a new conversation (not a continue), reset shell and file snapshots
    if not is_continue:
        clear_conversation_snapshots(conversation_id)
        try:
            session_manager.restart_persistent_shell()
            logger.info(f"[API] Restarted persistent shell for new conversation {conversation_id}")
        except Exception as e:
            logger.warning(f"[API] Failed to restart shell: {e}")
    
    messages = (chat_request.messages or []).copy()
    
    if chat_request.message:
        messages.append({
            "role": "user",
            "content": chat_request.message
        })
    
    logger.info(f"[API] Total messages: {len(messages)}, provider: {chat_request.provider}")
    
    # Use provided provider or default
    provider = chat_request.provider or DEFAULT_PROVIDER

    tools_override = get_filtered_tools(chat_request.tools) if chat_request.tools else None
    if tools_override is not None:
        tool_names = [td["function"]["name"] for td in tools_override]
        logger.info(f"[API] Tool override ({chat_request.tools}): {tool_names}")
    
    # Return streaming response
    return StreamingResponse(
        stream_chat_response(
            messages, conversation_id, request,
            provider=provider, tools_override=tools_override,
        ),
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
# File Diff API - Snapshot-based tracking per conversation turn
# ============================================================================

# File snapshots per conversation: {conversation_id: {file_path: content}}
# Captures file state at the start of each user turn
file_snapshots: Dict[str, Dict[str, str]] = {}

# Track which files have been touched (read or written) per conversation
files_touched: Dict[str, set] = {}


def snapshot_file(conversation_id: str, file_path: str, content: str):
    """
    Store a snapshot of a file's content at the start of a turn.
    Only stores if we don't already have a snapshot for this file in this conversation.
    """
    if conversation_id not in file_snapshots:
        file_snapshots[conversation_id] = {}
    
    # Only snapshot if we don't have this file yet (preserve original state)
    if file_path not in file_snapshots[conversation_id]:
        file_snapshots[conversation_id][file_path] = content
        logger.debug(f"[snapshot] Saved snapshot for {file_path} ({len(content)} chars)")


def mark_file_touched(conversation_id: str, file_path: str):
    """Mark a file as touched (read or written) in this conversation."""
    if conversation_id not in files_touched:
        files_touched[conversation_id] = set()
    files_touched[conversation_id].add(file_path)


def clear_conversation_snapshots(conversation_id: str):
    """Clear snapshots for a conversation (call when starting new turn or clearing chat)."""
    if conversation_id in file_snapshots:
        del file_snapshots[conversation_id]
    if conversation_id in files_touched:
        del files_touched[conversation_id]


def compute_unified_diff(original: str, current: str) -> list:
    """
    Compute a unified diff between original and current content.
    Returns list of lines with lineNumber, content, and type (added/removed/null).
    Shows full file with inline diff markers.
    """
    original_lines = original.split('\n') if original else []
    current_lines = current.split('\n') if current else []
    
    # Use difflib to get opcodes
    matcher = difflib.SequenceMatcher(None, original_lines, current_lines)
    opcodes = matcher.get_opcodes()
    
    result = []
    current_line_num = 1
    
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == 'equal':
            # Unchanged lines
            for idx in range(j1, j2):
                result.append({
                    "lineNumber": current_line_num,
                    "content": current_lines[idx],
                    "type": None
                })
                current_line_num += 1
                
        elif tag == 'replace':
            # Lines were replaced - show removed then added
            for idx in range(i1, i2):
                result.append({
                    "lineNumber": None,
                    "content": original_lines[idx],
                    "type": "removed"
                })
            for idx in range(j1, j2):
                result.append({
                    "lineNumber": current_line_num,
                    "content": current_lines[idx],
                    "type": "added"
                })
                current_line_num += 1
                
        elif tag == 'delete':
            # Lines were deleted
            for idx in range(i1, i2):
                result.append({
                    "lineNumber": None,
                    "content": original_lines[idx],
                    "type": "removed"
                })
                
        elif tag == 'insert':
            # Lines were added
            for idx in range(j1, j2):
                result.append({
                    "lineNumber": current_line_num,
                    "content": current_lines[idx],
                    "type": "added"
                })
                current_line_num += 1
    
    return result


def get_file_diffs_for_conversation(conversation_id: str) -> Dict[str, Any]:
    """
    Get diffs for all files touched in a conversation.
    Compares current file state against snapshots taken at turn start.
    """
    result = {"files": [], "error": None}
    
    work_dir = session_manager.get_session_working_directory()
    if not work_dir or not work_dir.exists():
        result["error"] = "No active session"
        return result
    
    touched = files_touched.get(conversation_id, set())
    snapshots = file_snapshots.get(conversation_id, {})
    
    for file_path in touched:
        try:
            full_path = work_dir / file_path
            
            # Get current content
            if full_path.exists() and full_path.is_file():
                try:
                    current_content = full_path.read_text(encoding='utf-8', errors='replace')
                except Exception as e:
                    logger.warning(f"Could not read file {file_path}: {e}")
                    continue
            else:
                # File was deleted
                current_content = ""
            
            # Get original content from snapshot
            original_content = snapshots.get(file_path, "")
            
            # Skip if no changes
            if original_content == current_content:
                continue
            
            # Compute diff
            lines = compute_unified_diff(original_content, current_content)
            
            has_changes = any(line["type"] in ["added", "removed"] for line in lines)
            
            if has_changes or not original_content:
                result["files"].append({
                    "id": file_path,
                    "path": file_path,
                    "lines": lines,
                    "hasChanges": has_changes,
                    "isNew": not original_content and current_content
                })
                
        except Exception as e:
            logger.error(f"Error processing file {file_path}: {e}")
    
    return result


@app.get("/api/files/diff")
async def get_file_diff(conversation_id: Optional[str] = None, file_path: Optional[str] = None):
    """
    Get diff for files modified since the start of the current conversation turn.
    
    Args:
        conversation_id: The conversation to get diffs for
        file_path: Optional specific file to get diff for
    
    Returns:
        - files: List of files with their diff lines
        - Each line has: lineNumber, content, type (added/removed/null)
    """
    if not conversation_id:
        return {"files": [], "error": "No conversation_id specified"}
    
    result = get_file_diffs_for_conversation(conversation_id)
    
    # Filter by specific file if requested
    if file_path and result["files"]:
        result["files"] = [f for f in result["files"] if f["path"] == file_path]
    
    return result


@app.post("/api/files/snapshot")
async def create_snapshot(conversation_id: str):
    """
    Create a new baseline snapshot for the conversation.
    Call this at the start of a new turn to reset the diff baseline.
    """
    clear_conversation_snapshots(conversation_id)
    return {"status": "success", "message": "Snapshots cleared for new turn"}


# ============================================================================
# File Tree API - Browse agent working space
# ============================================================================

def build_file_tree(directory: Path, base_path: Path, max_depth: int = 5, current_depth: int = 0) -> list:
    """
    Recursively build a file tree structure for the given directory.
    
    Returns list of dicts with: name, path, type (file/folder), children (for folders)
    """
    if current_depth >= max_depth:
        return []
    
    items = []
    try:
        # Sort: folders first, then files, both alphabetically
        entries = sorted(directory.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        
        for entry in entries:
            # Skip hidden files/folders and common non-essential dirs
            if entry.name.startswith('.') or entry.name in ('__pycache__', 'node_modules', '.git', '.venv', 'venv'):
                continue
            
            relative_path = str(entry.relative_to(base_path)).replace('\\', '/')
            
            if entry.is_dir():
                children = build_file_tree(entry, base_path, max_depth, current_depth + 1)
                items.append({
                    "name": entry.name,
                    "path": relative_path,
                    "type": "folder",
                    "children": children
                })
            else:
                # Get file extension for icon hints
                ext = entry.suffix.lower() if entry.suffix else ""
                items.append({
                    "name": entry.name,
                    "path": relative_path,
                    "type": "file",
                    "extension": ext
                })
    except PermissionError:
        pass
    except Exception as e:
        logger.warning(f"Error reading directory {directory}: {e}")
    
    return items


@app.get("/api/files/tree")
async def get_file_tree(max_depth: int = 5):
    """
    Get the folder structure of the agent's working space.
    
    Returns a hierarchical tree structure of files and folders.
    """
    work_dir = session_manager.get_session_working_directory()
    if not work_dir or not work_dir.exists():
        return {"tree": [], "root": None, "error": "No active session"}
    
    tree = build_file_tree(work_dir, work_dir, max_depth=max_depth)
    
    return {
        "tree": tree,
        "root": str(work_dir),
        "error": None
    }


@app.get("/api/files/read")
async def read_file_content(file_path: str):
    """
    Read content of a file from the agent's working space.
    """
    work_dir = session_manager.get_session_working_directory()
    if not work_dir or not work_dir.exists():
        raise HTTPException(status_code=400, detail="No active session")
    
    # Security: ensure path is within working directory
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
        content = full_path.read_text(encoding='utf-8', errors='replace')
        return {
            "path": file_path,
            "content": content,
            "size": full_path.stat().st_size
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {str(e)}")


# ============================================================================
# Workspace File Operations API - Upload, Delete, Download & Export
# ============================================================================

WORKSPACE_EXCLUDE = {
    '__pycache__', '.git', 'node_modules', '.venv', 'venv',
    '.thinktool_sessions', '.mypy_cache', '.pytest_cache',
}


@app.post("/api/workspace/upload")
async def upload_workspace(
    files: List[UploadFile] = File(...),
    clear: bool = Form(True),
):
    """Upload files from a folder into the agent workspace.

    Accepts multipart/form-data with multiple files. Each file's filename
    is treated as a relative path within the workspace (preserving the
    folder structure from the client-side webkitdirectory picker).
    """
    work_dir = session_manager.get_session_working_directory()
    if not work_dir:
        raise HTTPException(status_code=400, detail="No active workspace")

    work_dir.mkdir(parents=True, exist_ok=True)

    if clear:
        for child in list(work_dir.iterdir()):
            if child.name in WORKSPACE_EXCLUDE:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)

    count = 0
    for upload_file in files:
        # The filename from webkitdirectory includes the relative path
        # (e.g. "src/main.py").  FastAPI/Starlette preserves this.
        relative_path = getattr(upload_file, "filename", None)
        if not relative_path:
            continue

        # Security: prevent path traversal attacks
        safe_path = Path(relative_path)
        if safe_path.is_absolute() or ".." in safe_path.parts:
            logger.warning(f"[upload] Rejected unsafe path: {relative_path}")
            continue

        dest = work_dir / safe_path
        dest.parent.mkdir(parents=True, exist_ok=True)

        content = await upload_file.read()
        dest.write_bytes(content)
        count += 1

    return {
        "status": "success",
        "files_uploaded": count,
        "workspace": str(work_dir),
    }


class DeleteRequest(BaseModel):
    """Request body for deleting a file or folder."""
    path: str = Field(..., description="Relative path within the workspace")


@app.post("/api/files/delete")
async def delete_workspace_item(req: DeleteRequest):
    """Delete a file or folder from the agent workspace."""
    work_dir = session_manager.get_session_working_directory()
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
    from fastapi.responses import FileResponse

    work_dir = session_manager.get_session_working_directory()
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

    return FileResponse(
        path=str(full_path),
        filename=full_path.name,
        media_type="application/octet-stream",
    )


@app.get("/api/files/export")
async def export_workspace_folder(folder_path: str):
    """Export a folder from the workspace as a .zip archive."""
    from fastapi.responses import FileResponse
    import tempfile
    import zipfile

    work_dir = session_manager.get_session_working_directory()
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
        return FileResponse(
            path=tmp.name,
            filename=f"{full_path.name}.zip",
            media_type="application/zip",
            background=None,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to export: {str(e)}")


@app.get("/api/workspace/info")
async def workspace_info():
    """
    Return metadata about the current workspace (Docker mode, path, file count).
    """
    work_dir = session_manager.get_session_working_directory()
    file_count = 0
    if work_dir and work_dir.exists():
        file_count = sum(1 for _ in work_dir.rglob('*') if _.is_file())

    return {
        "docker_mode": DOCKER_MODE,
        "workspace": str(work_dir) if work_dir else None,
        "file_count": file_count,
    }


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
