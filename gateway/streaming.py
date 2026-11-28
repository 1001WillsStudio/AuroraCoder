"""
Streaming infrastructure for the Conversation Gateway.

Active stream management, SSE formatting, continuation detection, backend proxy,
and subscriber SSE — all the plumbing that powers the SSE-based chat streaming.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from weakref import WeakValueDictionary

import httpx

from src.backend_access import backend_auth_headers
from src.config import WORKSPACE_DIR, MAX_FILE_READ_SIZE, CONTINUATION_TOOL_RESULT
from src.workspace_paths import WorkspacePathError, resolve_under_workspace
from src.message_provenance import HANDOFF_PREFIX, TASK_RESUME_SOURCE, TURN_INPUT_RECEIPT
from gateway.conversation_history import begin_turn_credential, revoke_turn_credential
from gateway.conversation_store import store, sanitize_frontend_messages, ensure_error_frontend_message
from gateway.provider_registry import get_max_iterations
from gateway.task_checklist import TaskChecklist, TaskConflict
from gateway.task_terminal_jobs import pending_terminal_jobs
from gateway.workspace import (
    file_snapshots,
    mark_file_touched,
    snapshot_file,
    generate_workspace_tree_text,
)
from src.code_sandbox import WORKSPACE
from src.task_checklist import TASK_TOOL_NAMES, TaskProgress, TaskResume
from memory.ops.extractor import run_extraction
from memory.ops.consolidator import run_consolidation

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
    turn_token: str | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None
    new_conversation_id: Optional[str] = None   # set when continuation detected
    turn_id: Optional[str] = None
    task_token: Optional[str] = None
    task_request: Optional[str] = None
    task_request_is_retry: bool = False
    task_options: TaskResume = field(default_factory=TaskResume)
    initial_task_progress: Optional[TaskProgress] = None
    accept_turn_inputs: bool = True
    input_message_count: int = 0
    backend_stop_token: Optional[str] = None

    @property
    def task_active(self) -> bool:
        return self.initial_task_progress is not None


active_streams: Dict[str, ActiveStream] = {}
_streams_lock = asyncio.Lock()
_admission_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_CANCEL_TIMEOUT_SECONDS = 2.0
_CANCEL_DRAIN_SECONDS = 0.35


def _turn_admission_lock(conversation_id: str) -> asyncio.Lock:
    lock = _admission_locks.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _admission_locks[conversation_id] = lock
    return lock


# ============================================================================
# Passive Memory Distillation (Layer 2a) — dispatched at session end
# ============================================================================
# Runs off the hot path in a small dedicated thread pool: extraction makes a
# blocking LLM call, so it must never run on the asyncio event loop thread.
# Deliberately separate from _get_executor() in tool_executor.py — that pool
# sizes itself to MAX_TOOL_CONCURRENCY for in-turn tool calls; this one is for
# fire-and-forget post-session housekeeping and should never compete with it.
_memory_ops_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="memory-ops")

# Only distill top-level user chats — never subagents mid-flight, and never
# conversations that errored out with no real content. "continued" is
# deliberately NOT in this set (a conversation handed off via
# continue_as_new_chat isn't "really" done from the user's point of view) —
# but that segment's transcript still needs mining, so it's triggered
# directly at the handoff point instead of through this gate. See the
# continuation-detection block in _proxy_backend_stream.
_EXTRACTION_ELIGIBLE_STATUSES = {"completed", "max_iterations_reached", "interrupted"}


def _run_session_end_memory_ops(conversation_id: str, conv_type: str, status: str) -> None:
    """Background job: extraction, then a consolidation pass.

    Consolidation is now dispatched UNCONDITIONALLY at session end, not
    gated on ``written``. The whole point of consolidation is the exact
    case extraction did NOT write anything new: dedupe/merge keeps the
    corpus tight and decay retires memories that have gone idle, and
    neither has any causal link to "this turn produced a new memory."
    When extraction returns nothing, that is precisely when the corpus is
    most likely overdue for housekeeping — so the pass must still run.
    (Both passes are internally fail-open and log-only; a no-op turn is
    cheap.)

    Swallows all exceptions — this must never affect the conversation
    that already finished and was already persisted successfully.
    """
    try:
        run_extraction(conversation_id, store.get_messages(conversation_id))
        run_consolidation()
    except Exception:
        logger.exception(f"[memory-ops] Session-end distillation failed for {conversation_id[:8]}...")


def _schedule_memory_distillation(conversation_id: str, conv_type: str, status: str) -> None:
    """Enqueue passive distillation for a just-finished conversation.

    Fire-and-forget: submitted to a background thread pool and never
    awaited by the streaming path, so a slow or failing extraction pass
    can never delay the response the user already received.
    """
    if conv_type != "user_chat" or status not in _EXTRACTION_ELIGIBLE_STATUSES:
        return
    _memory_ops_executor.submit(_run_session_end_memory_ops, conversation_id, conv_type, status)


_FILE_WRITE_TOOLS = {"write_file", "edit_file", "delete_file"}

# Tool-call IDs for which we've already taken a pre-write snapshot.
# Prevents re-snapshotting after the write has already happened.
_snapshotted_tool_calls: Dict[str, set] = {}  # {conversation_id: {tool_call_id, ...}}

_TRACKING_TOO_LARGE_PLACEHOLDER = (
    "[File too large to track — {size:,} bytes ({size_mb:.1f} MB). "
    "Snapshot skipped to avoid OOM.]"
)


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

            try:
                full_path = resolve_under_workspace(work_dir, file_path)
            except WorkspacePathError:
                continue

            # Phase 1: snapshot BEFORE the write (tool_call seen, no result yet)
            if tc_id not in _snapshotted_tool_calls[conversation_id]:
                _snapshotted_tool_calls[conversation_id].add(tc_id)
                if file_path not in file_snapshots.get(conversation_id, {}):
                    try:
                        if full_path.exists() and full_path.is_file():
                            size = full_path.stat().st_size
                            if size > MAX_FILE_READ_SIZE:
                                content = _TRACKING_TOO_LARGE_PLACEHOLDER.format(
                                    size=size, size_mb=size / (1024 * 1024)
                                )
                            else:
                                content = full_path.read_text(encoding="utf-8", errors="replace")
                        else:
                            content = ""
                        snapshot_file(conversation_id, file_path, content)
                    except Exception:
                        snapshot_file(conversation_id, file_path, "")

            # Phase 2: mark touched once the tool has completed
            if tc_id in result_ids:
                mark_file_touched(conversation_id, file_path)


async def _drain_stopped_backend(stream: ActiveStream) -> None:
    try:
        async with httpx.AsyncClient(timeout=_CANCEL_DRAIN_SECONDS, trust_env=False) as client:
            response = await client.post(
                f"{BACKEND_URL}/internal/chat/{stream.conversation_id}/stop",
                json={"stop_token": stream.backend_stop_token},
                headers=backend_auth_headers(),
            )
        if response.status_code == 204 and stream.task is not None:
            # Timing out this waiter must leave the proxy available for forced cancellation.
            await asyncio.wait({stream.task})
    except httpx.HTTPError:
        logger.debug("[cancel] Could not request a final snapshot from the backend", exc_info=True)


async def _cancel_active_stream(conversation_id: str) -> None:
    """Stop this conversation and its children within a single cancellation deadline."""
    deadline = time.perf_counter() + _CANCEL_TIMEOUT_SECONDS
    async with _streams_lock:
        parent = active_streams.get(conversation_id)
        lineage = {conversation_id}
        if parent and parent.new_conversation_id:
            lineage.add(parent.new_conversation_id)
        streams = [
            stream for stream in active_streams.values()
            if not stream.finished and (stream.conversation_id in lineage or stream.parent_id in lineage)
        ]
        for stream in streams:
            stream.cancel_event.set()

    # Let the backend send a final snapshot containing any completed tool results.
    drains = [
        _drain_stopped_backend(stream) for stream in streams
        if stream.backend_stop_token and stream.task and not stream.task.done()
    ]
    if drains:
        grace = min(_CANCEL_DRAIN_SECONDS, max(0, (deadline - time.perf_counter()) / 2))
        try:
            await asyncio.wait_for(asyncio.gather(*drains), timeout=grace)
        except asyncio.TimeoutError:
            pass

    pending = {stream.task for stream in streams if stream.task and not stream.task.done()}
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending, timeout=max(0, deadline - time.perf_counter()))

    # Release ownership even if a transport ignores the hard cancellation.
    async with _streams_lock:
        for stream in streams:
            cid = stream.conversation_id
            if stream.finished or active_streams.get(cid) is not stream:
                continue
            stream.finished = True
            revoke_turn_credential(stream.turn_token)
            del active_streams[cid]
            if stream.status == "running":
                stream.status = "interrupted"
                _persist_finished_stream(stream, stream.status)
            logger.warning("[cancel] Force-removed stream %.8s after cancellation", cid)


# ============================================================================
# SSE Helpers
# ============================================================================

def _format_sse(event_type: str, data: Any) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


_TERMINAL_EVENTS = {"done", "error"}


def _broadcast_stream_event(stream: ActiveStream, event_type: str, data: dict) -> None:
    for queue in list(stream.subscribers):
        if queue.full():
            if event_type not in _TERMINAL_EVENTS and event_type != "messages":
                continue
            # Keep the latest full snapshot or terminal outcome for a slow subscriber.
            queue.get_nowait()
        queue.put_nowait((event_type, data))


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

def _scan_for_continuation(raw_messages: list, prior_tool_ids: set[str]) -> str | None:
    """
    Use the latest handoff from this turn only after its tool completed.
    """
    results = {
        msg.get("tool_call_id"): msg.get("content")
        for msg in raw_messages if msg.get("role") == "tool"
    }
    for msg in reversed(raw_messages):
        if msg.get("role") != "assistant":
            continue
        for tc in reversed(msg.get("tool_calls") or []):
            function = tc.get("function", {})
            tool_id = tc.get("id")
            if function.get("name") != "continue_as_new_chat" or tool_id in prior_tool_ids:
                continue
            if not tool_id or results.get(tool_id) != CONTINUATION_TOOL_RESULT:
                continue
            try:
                args = json.loads(function["arguments"])
            except (KeyError, TypeError, ValueError):
                continue
            prompt = args.get("prompt") if isinstance(args, dict) else None
            if isinstance(prompt, str) and prompt.strip():
                return prompt
    return None


_CONTINUED_MARKER = HANDOFF_PREFIX
_PRIOR_USER_PREFIX = "Prior user messages (chronological): "


def _continuation_message(raw_messages: list, summary: str) -> str:
    user_messages = []
    for message in raw_messages:
        content = message.get("content")
        if (message.get("role") != "user" or message.get("source") == TASK_RESUME_SOURCE
                or not isinstance(content, str) or not content.strip()):
            continue
        if not content.startswith(_CONTINUED_MARKER):
            user_messages.append(content)
            continue
        line = content[len(_CONTINUED_MARKER):].lstrip().split("\n", 1)[0]
        if line.startswith(_PRIOR_USER_PREFIX):
            try:
                previous = json.loads(line[len(_PRIOR_USER_PREFIX):])
            except ValueError:
                continue
            if isinstance(previous, list):
                user_messages.extend(text for text in previous if isinstance(text, str))

    clipped = [
        text if len(text) <= 3000 else f"{text[:1500]}\n[Earlier text omitted]\n{text[-1450:]}"
        for text in user_messages
    ]
    context = clipped[:1]
    remaining = 12000 - sum(map(len, context))
    recent = []
    for text in reversed(clipped[1:]):
        if len(text) > remaining:
            break
        recent.append(text)
        remaining -= len(text)
    context.extend(reversed(recent))
    return (
        f"{_CONTINUED_MARKER}\n\n{_PRIOR_USER_PREFIX}{json.dumps(context, ensure_ascii=False)}\n\n"
        "Keep the user's earlier scope and the latest corrections. The following summary "
        f"was written by the previous agent and may omit details.\n\n{summary}"
    )


def task_workspace() -> str:
    return str(Path(WORKSPACE).resolve())


def start_task_segment(task: TaskChecklist, *, fresh: bool, handoff: str = "", stalled: int = 0) -> str:
    """Register the new stream before yielding, so pause and resume cannot race a handoff."""
    if task.workspace != task_workspace():
        raise TaskConflict("Reopen the task in its original workspace before resuming.")
    provider = task.options.provider_id or task.options.provider
    cid = str(uuid.uuid4()) if fresh else task.conversation_id
    if fresh:
        store.create_conversation(conversation_id=cid, provider_id=provider)
    message = f"Continue task: {task.objective}" if fresh else task.objective
    prior = _fix_orphan_tool_calls(store.get_messages(cid))
    synthetic = {"role": "user", "content": message, "source": TASK_RESUME_SOURCE}
    applied_steering, pending_steering = task.split_steering_at_pending()
    directions = [entry.as_message() for entry in pending_steering]
    store.save_messages(cid, [*prior, synthetic, *directions])
    store.seed_frontend_user_message(cid, message, source=TASK_RESUME_SOURCE)
    for direction in directions:
        store.seed_frontend_user_message(cid, direction["content"])
    store.acknowledge_turn_inputs(task.conversation_id, directions)
    applied_steering.extend(entry.model_copy(update={"applied_to": cid}) for entry in pending_steering)
    task = store.tasks.save(
        task, status="active", reason="", stalled=stalled, handoff=handoff, started=True,
        conversation_ids=[*task.conversation_ids, cid] if fresh else task.conversation_ids,
        steering_entries=applied_steering,
    )
    store.update_status(cid, "running", accepted_turn_id=None)
    stream = ActiveStream(
        conversation_id=cid, provider=provider, task_token=secrets.token_urlsafe(32),
        initial_task_progress=task.progress,
    )
    try:
        tree = generate_workspace_tree_text(WORKSPACE) if not prior else ""
    except Exception:
        logger.exception("Could not build workspace tree for task %s", task.id)
        tree = ""
    body = {
        **task.options.model_dump(exclude_none=True),
        "conversation_id": cid, "message": message, "messages": prior, "max_iterations": get_max_iterations(),
        "message_source": TASK_RESUME_SOURCE,
        "workspace_tree": tree, "task_state": task.prompt_state(), "task_token": stream.task_token,
        "accept_turn_inputs": True,
    }
    if directions:
        body.update(messages=[*prior, synthetic, *directions[:-1]], message=directions[-1]["content"],
                    message_receipt=directions[-1].get(TURN_INPUT_RECEIPT))
        stream.task_request = directions[-1]["content"]
        body.pop("message_source")
    active_streams[cid] = stream
    launch_backend_stream(stream, body)
    return cid


def pause_task(conversation_id: str, reason: str = "paused") -> TaskChecklist | None:
    task = store.tasks.get(conversation_id)
    if task and task.status == "active":
        task = store.tasks.save(task, status="paused", reason=reason)
    return task


def _task_work_observations(messages: list, prior_tool_ids: set[str]) -> list[str]:
    names = {}
    observations = {}
    for message in messages:
        if message.get("role") == "user":
            names.clear()
            observations.clear()
        elif message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                call_id = call.get("id")
                name = call.get("function", {}).get("name")
                if call_id and call_id not in prior_tool_ids and name and name not in TASK_TOOL_NAMES:
                    if name != "continue_as_new_chat":
                        names[call_id] = name
        elif message.get("role") == "tool":
            name = names.get(message.get("tool_call_id"))
            result = message.get("content")
            if not name or not isinstance(result, str):
                continue
            result = result.strip()
            if (not result or result.startswith(("Error", "Skipped:", "Command timed out"))
                    or re.search(r"^Exit Code: (?!0$)-?\d+$", result, re.MULTILINE)):
                continue
            value = f"{name}\0{' '.join(result.split())}"
            observations[hashlib.sha256(value.encode()).hexdigest()] = None
    return list(observations)


def _finish_task_segment(stream: ActiveStream, raw_messages: list, prior_tool_ids: set[str]) -> None:
    cid = stream.conversation_id
    task = store.tasks.get(cid)
    if not task or task.status != "active" or task.conversation_id != cid:
        return
    steering = bool(task.pending_steering)
    if stream.status not in {"completed", "max_iterations_reached", "context_limit_reached"}:
        pause_task(cid, "error")
    elif task.verified and not steering:
        store.tasks.save(task, status="completed", reason="")
    elif task.progress.blocker and not steering:
        pause_task(cid, "blocked")
        stream.status = "interrupted"
    else:
        previous = stream.initial_task_progress
        observations = _task_work_observations(raw_messages, prior_tool_ids)
        new_observation = bool(set(observations).difference(task.work_observations))
        progressed = previous is None or (
            {item.text: item.evidence for item in task.progress.items}
            != {item.text: item.evidence for item in previous.items}
        ) or (
            task.progress.checkpoint != previous.checkpoint and new_observation
        )
        recent = list(dict.fromkeys([*task.work_observations, *observations]))[-128:]
        if recent != task.work_observations:
            task = store.tasks.save(task, work_observations=recent)
        stalled = 0 if steering or progressed else task.stalled + 1
        if stalled >= 2:
            pause_task(cid, "no_progress")
            stream.status = "interrupted"
            return
        prompt = _scan_for_continuation(raw_messages, prior_tool_ids)
        handoff = prompt[-6000:] if isinstance(prompt, str) else task.handoff
        store.save_messages(cid, raw_messages)
        if steering and task.progress.blocker:
            task = store.tasks.save(task, progress=task.progress.model_copy(update={"blocker": ""}))
        stream.new_conversation_id = start_task_segment(task, fresh=True, handoff=handoff, stalled=stalled)
        stream.status = "continued"
        store.update_status(cid, "continued")
        _memory_ops_executor.submit(_run_session_end_memory_ops, cid, "user_chat", "continued")


def _record_task_terminal_jobs(stream: ActiveStream, messages: list[dict], prior_tool_ids: set[str]) -> None:
    task = store.tasks.get(stream.conversation_id)
    if not task or task.status != "active" or task.conversation_id != stream.conversation_id:
        return
    jobs = pending_terminal_jobs(task.terminal_jobs, messages, prior_tool_ids)
    if jobs != task.terminal_jobs:
        store.tasks.save(task, terminal_jobs=jobs)


async def _start_continuation(source: ActiveStream, prompt: str, messages: list[dict], options: dict) -> None:
    """Persist an ordinary chat handoff before dispatching its successor."""
    cid = source.conversation_id
    provider = source.provider
    user_msg = _continuation_message(messages, prompt)
    inputs = store.pending_turn_inputs(cid)
    new_cid = store.create_conversation(provider_id=provider)
    store.save_messages(new_cid, [{"role": "user", "content": user_msg}, *inputs])
    store.save_frontend_messages(new_cid, [{"role": "user", "content": user_msg}])
    for pending in inputs:
        store.seed_frontend_user_message(new_cid, pending["content"])
    store.save_messages(cid, messages)
    store.mark_continued(cid, new_cid, inputs=inputs)
    source.new_conversation_id = new_cid

    # The finalizer skips continued chats, so distill the saved source here.
    _memory_ops_executor.submit(_run_session_end_memory_ops, cid, "user_chat", "continued")

    body = {
        **{key: options[key] for key in ("provider_id", "model", "max_iterations") if key in options},
        "conversation_id": new_cid,
        "message": user_msg,
        "provider": provider,
    }
    successor = ActiveStream(
        conversation_id=new_cid,
        provider=provider,
        task_token=secrets.token_urlsafe(32),
    )
    body.update(task_token=successor.task_token, accept_turn_inputs=True)
    if inputs:
        successor.task_request = inputs[-1]["content"]
        body.update(
            messages=[{"role": "user", "content": user_msg}, *inputs[:-1]],
            message=inputs[-1]["content"], message_receipt=inputs[-1][TURN_INPUT_RECEIPT],
        )
    else:
        body["task_state"] = {"status": "conversation_handoff"}

    async with _streams_lock:
        active_streams[new_cid] = successor

    launch_backend_stream(successor, body)
    logger.info("[proxy] Started continuation %s from %s", new_cid[:8], cid[:8])

# ============================================================================
# Backend Proxy — background task
# ============================================================================

def launch_backend_stream(stream: ActiveStream, body: dict, *, proxy=None) -> None:
    """Start the admitted run and bind history access to its lifetime."""
    body.pop("conversation_history_token", None)
    if stream.conv_type == "user_chat" and not stream.parent_id and not body.get("tools"):
        stream.turn_token = begin_turn_credential(stream.conversation_id)
        body["conversation_history_token"] = stream.turn_token
    task = None
    try:
        task = (proxy or _proxy_backend_stream)(stream, body)
        stream.task = asyncio.create_task(task)
    except BaseException:
        if task is not None:
            task.close()
        revoke_turn_credential(stream.turn_token)
        raise
    stream.task.add_done_callback(lambda _: revoke_turn_credential(stream.turn_token))


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
    prior_tool_ids = {
        call["id"] for message in request_body.get("messages") or []
        for call in message.get("tool_calls") or [] if call.get("id")
    }
    last_saved_msg_count = 0
    stream.backend_stop_token = secrets.token_urlsafe(32)
    backend_connect_start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(None)) as client:
            async with client.stream(
                "POST",
                f"{BACKEND_URL}/api/chat",
                json={
                    **request_body, "compact_delta_history": True,
                    "stream_stop_token": stream.backend_stop_token,
                },
                headers={"Content-Type": "application/json", **backend_auth_headers()},
            ) as response:
                backend_connect_elapsed = time.perf_counter() - backend_connect_start
                logger.info(f"[proxy] [{cid[:8]}...] backend_connect={backend_connect_elapsed:.3f}s status={response.status_code}")
                if response.status_code != 200:
                    body = (await response.aread()).decode(errors="replace")
                    err = {"message": f"Backend returned {response.status_code}: {body[:500]}", "type": "BackendError"}
                    stream.latest_event_type = "error"
                    stream.latest_event_data = err
                    _broadcast_stream_event(stream, "error", err)
                    return

                buffer = ""
                snapshot_prefix = None
                snapshot_seq = None
                async for chunk in response.aiter_text():
                    if stream.finished:
                        break

                    buffer += chunk
                    while "\n\n" in buffer:
                        raw, buffer = buffer.split("\n\n", 1)
                        for etype, edata in _parse_sse_blocks(raw + "\n\n"):
                            stopping = stream.cancel_event.is_set()
                            if stopping and not (
                                etype in {"messages", "done"} and edata.get("status") == "interrupted"
                            ):
                                continue
                            raw_msgs = edata.get("raw_messages", [])
                            compact = edata.pop("raw_delta", None) if etype == "delta" else None
                            if compact is not None:
                                if snapshot_prefix is None or compact["snapshot_seq"] != snapshot_seq:
                                    continue
                                # Keep partial assistant output available for stop and reconnect.
                                raw_msgs = [*snapshot_prefix, compact["message"]]
                                edata["raw_messages"] = raw_msgs
                            if raw_msgs and len(raw_msgs) < stream.input_message_count:
                                # An older SSE frame can arrive after the boundary write.
                                continue
                            if raw_msgs and compact is None:
                                snapshot_prefix = raw_msgs[:-1]
                                snapshot_seq = edata.get("seq")
                            stream.latest_event_type = etype
                            stream.latest_event_data = edata
                            stream.status = edata.get("status", stream.status)
                            stream.provider = edata.get("provider", stream.provider)
                            if stream.task_active and etype in {"messages", "done"}:
                                _record_task_terminal_jobs(stream, raw_msgs, prior_tool_ids)
                            if stream.task_active and etype == "done" and not stopping:
                                _finish_task_segment(stream, raw_msgs, prior_tool_ids)
                                edata["status"] = stream.status

                            if (etype == "messages" and edata.get("status") == "completed"
                                    and stream.conv_type == "user_chat" and not stream.task_active
                                    and not stream.new_conversation_id and request_body.get("tools") not in {
                                        "read_only", "gap_investigation", "memory_maintenance",
                                    }):
                                prompt = _scan_for_continuation(raw_msgs, prior_tool_ids)
                                if prompt:
                                    await _start_continuation(stream, prompt, raw_msgs, request_body)

                            # Annotate events with new_conversation_id if continuation was detected
                            if stream.new_conversation_id:
                                stream.status = "continued"
                                edata["status"] = "continued"
                                edata["new_conversation_id"] = stream.new_conversation_id

                            # ── Always update latest state from *every* event ──
                            if edata.get("messages"):
                                edata["messages"] = sanitize_frontend_messages(edata["messages"])
                                stream.latest_frontend_messages = edata["messages"]


                            # --- File tracking for diff endpoint ---
                            if raw_msgs:
                                try:
                                    _track_file_changes(cid, raw_msgs)
                                except Exception:
                                    pass

                            # --- Incremental persistence on EVERY event ---
                            if raw_msgs and len(raw_msgs) > last_saved_msg_count:
                                last_saved_msg_count = len(raw_msgs)
                                try:
                                    store.save_messages(cid, raw_msgs)
                                    if edata.get("messages"):
                                        store.save_frontend_messages(cid, edata["messages"])
                                    if stream.status != "continued":
                                        store.update_status(cid, stream.status)
                                except Exception as exc:
                                    logger.warning(
                                        f"[proxy] Incremental save failed for "
                                        f"{cid[:8]}... ({len(raw_msgs)} msgs): {exc}"
                                    )

                            # ── Forward to frontend subscribers ──
                            # Forward just the display delta to subscribers.
                            if etype == "delta":
                                frontend_edata = {
                                    "seq": edata.get("seq"),
                                    "conversation_id": edata.get("conversation_id"),
                                    "status": edata.get("status"),
                                    "delta": edata.get("delta"),
                                }
                            else:
                                frontend_edata = edata

                            _broadcast_stream_event(stream, etype, frontend_edata)

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
        _broadcast_stream_event(stream, "error", err)

    except asyncio.CancelledError:
        logger.info(f"[proxy] Cancelled {cid[:8]}...")

    except Exception as e:
        logger.exception(f"[proxy] Error for {cid[:8]}...")
        err = {"message": str(e), "type": type(e).__name__}
        stream.status = "error"
        stream.latest_event_type = "error"
        stream.latest_event_data = err
        _broadcast_stream_event(stream, "error", err)

    finally:
        revoke_turn_credential(stream.turn_token)
        owner = active_streams.get(cid)
        superseded = stream.finished or (owner is not None and owner is not stream)
        stream.finished = True

        # Determine final status
        persist_status = stream.status
        if stream.cancel_event.is_set() and persist_status == "running":
            persist_status = "interrupted"
        elif persist_status == "running":
            persist_status = "error"

        # The latest state includes reconstructed history when the last event was a delta.
        if not superseded:
            task = store.tasks.get(cid) if stream.task_active else None
            if task and task.conversation_id == cid and task.status == "active":
                pause_task(cid, "paused" if stream.cancel_event.is_set() else "error")
            _persist_finished_stream(stream, persist_status)

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


def _persist_finished_stream(stream: ActiveStream, persist_status: str) -> None:
    cid = stream.conversation_id
    try:
        current_status = None
        try:
            conv = store.get_conversation(cid)
            current_status = conv.get("status")
        except Exception:
            pass
        if current_status != "continued":
            store.update_status(cid, persist_status)
        if persist_status == "error":
            data = stream.latest_event_data if isinstance(stream.latest_event_data, dict) else {}
            err = data if stream.latest_event_type == "error" else (
                {"message": data["error"]} if data.get("error") else None
            )
            fe = ensure_error_frontend_message(
                stream.latest_frontend_messages or store.get_frontend_messages(cid),
                err,
            )
            stream.latest_frontend_messages = fe
            store.save_frontend_messages(cid, fe)
        elif stream.latest_frontend_messages:
            store.save_frontend_messages(cid, stream.latest_frontend_messages)
        raw_msgs = stream.latest_event_data.get("raw_messages", []) if stream.latest_event_data else []
        if raw_msgs and len(raw_msgs) >= stream.input_message_count:
            store.save_messages(cid, raw_msgs)

        if current_status != "continued":
            _schedule_memory_distillation(cid, stream.conv_type, persist_status)
    except Exception as e:
        logger.error(f"[proxy] Final persist failed for {cid[:8]}...: {e}")


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
            if (queue.empty() and stream.latest_event_type in _TERMINAL_EVENTS
                    and stream.latest_event_data):
                yield _format_sse(stream.latest_event_type, stream.latest_event_data)
                return
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

        idle_ticks = 0
        while not stream.finished or not queue.empty():
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
