#!/usr/bin/env python3
"""
schedule-daemon — stdio MCP server for agent self-wakeup scheduling.

Provides three tools visible to the agent via the ToolStore MCP pipeline:

    schedule_wakeup  — register a future wake-up message for a conversation
    list_scheduled   — list pending scheduled tasks for a conversation
    cancel_scheduled — cancel a pending scheduled task

A background daemon thread monitors due tasks and fires them by POSTing
to the AuroraCoder gateway's ``/api/chat`` endpoint, resuming the
conversation as if the user had sent a message.

Designed to be started by the ToolStore MCPClient via stdio transport
and kept alive indefinitely by the connection pool.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

# ── Constants ─────────────────────────────────────────────────────────────────

SERVER_NAME = "schedule-daemon"
SERVER_VERSION = "1.0.0"

# How often the daemon thread checks for due tasks (seconds)
POLL_INTERVAL = 15

# Where to find the AuroraCoder gateway
GATEWAY_URL = os.environ.get("CONVO_SERVER_URL", "http://localhost:8081")

# Where to persist schedules
DATA_DIR = Path(
    os.environ.get("SCHEDULE_DATA_DIR", os.path.expanduser("~/.schedule-daemon"))
)
DATA_DIR.mkdir(parents=True, exist_ok=True)
SCHEDULES_FILE = DATA_DIR / "schedules.json"


# ── In-memory schedule store (thread-safe) ───────────────────────────────────

class ScheduleStore:
    """Thread-safe in-memory store backed by a JSON file."""

    def __init__(self, filepath: Path):
        self._filepath = filepath
        self._lock = threading.Lock()
        self._tasks: Dict[str, dict] = {}
        self._load()

    # ── I/O ──────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._filepath.exists():
            try:
                with open(self._filepath, encoding="utf-8") as f:
                    self._tasks = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._tasks = {}

    def _save(self) -> None:
        try:
            tmp = self._filepath.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._tasks, f, indent=2, ensure_ascii=False)
            tmp.replace(self._filepath)
        except OSError:
            pass

    # ── CRUD ─────────────────────────────────────────────────────────────

    def add(self, task: dict) -> str:
        """Add a task and return its id."""
        import uuid as _uuid
        tid = _uuid.uuid4().hex[:12]
        task["id"] = tid
        task.setdefault("status", "pending")
        task.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        with self._lock:
            self._tasks[tid] = task
            self._save()
        return tid

    def list_for(self, conversation_id: str) -> List[dict]:
        with self._lock:
            return [
                t for t in self._tasks.values()
                if t.get("conversation_id") == conversation_id
                and t.get("status") == "pending"
            ]

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = "cancelled"
                self._save()
                return True
            return False

    def get_due(self) -> List[dict]:
        """Return pending tasks whose trigger_at has passed."""
        now = datetime.now(timezone.utc)
        due: List[dict] = []
        with self._lock:
            for t in list(self._tasks.values()):
                if t.get("status") != "pending":
                    continue
                try:
                    trigger_at = datetime.fromisoformat(t["when"])
                    if trigger_at.tzinfo is None:
                        trigger_at = trigger_at.replace(tzinfo=timezone.utc)
                except (ValueError, KeyError):
                    continue
                if trigger_at <= now:
                    due.append(t)
        return due

    def mark_fired(self, task_id: str) -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = "fired"
                self._tasks[task_id]["fired_at"] = datetime.now(timezone.utc).isoformat()
                self._save()


# ═══════════════════════════════════════════════════════════════════════════════
# MCP Server — JSON-RPC handler
# ═══════════════════════════════════════════════════════════════════════════════

store = ScheduleStore(SCHEDULES_FILE)


def handle_request(request: dict) -> dict | None:
    """Process a single JSON-RPC request, return the response or None for notifications."""
    method = request.get("method", "")
    req_id = request.get("id")

    # ── initialize ────────────────────────────────────────────────────────
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
                "instructions": (
                    "## Schedule Self-Wakeup\n\n"
                    "You can schedule a wake-up message to be delivered to "
                    "this conversation at a future time. Use these tools:\n\n"
                    "- `schedule_wakeup(conversation_id, when, message)` — "
                    "register a wake-up.  *when* is an ISO 8601 datetime "
                    "(e.g. 2026-07-23T08:00:00Z) or a relative time "
                    "(e.g. 'in 6 hours').\n"
                    "- `list_scheduled(conversation_id)` — see your pending tasks.\n"
                    "- `cancel_scheduled(task_id)` — cancel a pending task.\n\n"
                    "Your conversation ID is `{conversation_id}`."
                ),
            },
        }

    # ── notifications/initialized ────────────────────────────────────────
    if method == "notifications/initialized":
        return None

    # ── tools/list ────────────────────────────────────────────────────────
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "schedule_wakeup",
                        "description": (
                            "Schedule a wake-up message to be sent to this "
                            "conversation at a future time. When the schedule "
                            "triggers, the message will appear as a user message "
                            "in the conversation, resuming the agent.\n\n"
                            "Use for: monitoring long-running training, periodic "
                            "CI checks, delayed follow-ups, reminders."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "conversation_id": {
                                    "type": "string",
                                    "description": "The ID of the conversation to wake up. Use the value from the system prompt.",
                                },
                                "when": {
                                    "type": "string",
                                    "description": (
                                        "When to trigger. ISO 8601 datetime "
                                        "(e.g. '2026-07-23T08:00:00Z') or "
                                        "relative time (e.g. 'in 6 hours', "
                                        "'in 30 minutes')."
                                    ),
                                },
                                "message": {
                                    "type": "string",
                                    "description": (
                                        "The user message to inject when the "
                                        "schedule triggers. Be specific about "
                                        "what you want the awakened agent to do."
                                    ),
                                },
                            },
                            "required": ["conversation_id", "when", "message"],
                        },
                    },
                    {
                        "name": "list_scheduled",
                        "description": (
                            "List all pending scheduled tasks for a conversation."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "conversation_id": {
                                    "type": "string",
                                    "description": "The conversation ID to list schedules for.",
                                },
                            },
                            "required": ["conversation_id"],
                        },
                    },
                    {
                        "name": "cancel_scheduled",
                        "description": "Cancel a pending scheduled task by its ID.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "task_id": {
                                    "type": "string",
                                    "description": "The ID of the scheduled task to cancel.",
                                },
                            },
                            "required": ["task_id"],
                        },
                    },
                ]
            },
        }

    # ── tools/call ────────────────────────────────────────────────────────
    if method == "tools/call":
        params = request.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "schedule_wakeup":
            return _handle_schedule_wakeup(req_id, arguments)
        elif tool_name == "list_scheduled":
            return _handle_list_scheduled(req_id, arguments)
        elif tool_name == "cancel_scheduled":
            return _handle_cancel_scheduled(req_id, arguments)
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }

    # ── fallback ──────────────────────────────────────────────────────────
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


# ── Tool handlers ────────────────────────────────────────────────────────────


def _handle_schedule_wakeup(req_id, args: dict) -> dict:
    conversation_id = args.get("conversation_id", "")
    when = args.get("when", "")
    message = args.get("message", "")

    if not conversation_id or not when or not message:
        return _error(req_id, "Missing required arguments: conversation_id, when, message")

    # Resolve relative time expressions like "in 6 hours"
    trigger_at = _resolve_when(when)
    if not trigger_at:
        return _error(
            req_id,
            f"Could not parse 'when': {when}. Use ISO 8601 (2026-07-23T08:00:00Z) "
            "or relative time ('in 6 hours').",
        )

    task_id = store.add({
        "conversation_id": conversation_id,
        "when": trigger_at,
        "message": message,
        "original_when": when,
    })

    return _ok(req_id, (
        f"Schedule created (id={task_id}).\n"
        f"  When:   {trigger_at}\n"
        f"  Message: {message[:80]}{'...' if len(message) > 80 else ''}\n\n"
        f"The conversation will be resumed at {trigger_at}."
    ))


def _handle_list_scheduled(req_id, args: dict) -> dict:
    conversation_id = args.get("conversation_id", "")
    if not conversation_id:
        return _error(req_id, "Missing required argument: conversation_id")

    tasks = store.list_for(conversation_id)
    if not tasks:
        return _ok(req_id, "No pending scheduled tasks for this conversation.")

    lines = [f"Pending scheduled tasks ({len(tasks)}):"]
    for t in tasks:
        lines.append(f"  [{t['id']}] {t.get('when','?')} — {t.get('message','')[:60]}")
    return _ok(req_id, "\n".join(lines))


def _handle_cancel_scheduled(req_id, args: dict) -> dict:
    task_id = args.get("task_id", "")
    if not task_id:
        return _error(req_id, "Missing required argument: task_id")

    if store.cancel(task_id):
        return _ok(req_id, f"Task {task_id} cancelled.")
    return _error(req_id, f"Task {task_id} not found or already completed.")


# ── JSON-RPC helpers ─────────────────────────────────────────────────────────


def _ok(req_id, text: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {"content": [{"type": "text", "text": text}]},
    }


def _error(req_id, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32000, "message": message},
    }


# ── Relative time resolver ───────────────────────────────────────────────────


def _resolve_when(raw: str) -> str | None:
    """Parse 'when' into an ISO 8601 datetime string.

    Supports:
      - ISO 8601:               2026-07-23T08:00:00Z  (returned as-is)
      - Relative:               in 30 minutes, in 6 hours, in 2 days
    """
    import re

    raw = raw.strip()

    # Already ISO-ish?
    if re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", raw):
        return raw if raw.endswith("Z") or "+" in raw else raw + "Z"

    # Relative: "in X (seconds|minutes|hours|days)"
    m = re.match(r"^in\s+(\d+)\s*(seconds?|minutes?|hours?|days?)$", raw, re.IGNORECASE)
    if m:
        value = int(m.group(1))
        unit = m.group(2).lower().rstrip("s")  # strip plural 's'
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        if unit == "second":
            dt = now + timedelta(seconds=value)
        elif unit == "minute":
            dt = now + timedelta(minutes=value)
        elif unit == "hour":
            dt = now + timedelta(hours=value)
        elif unit == "day":
            dt = now + timedelta(days=value)
        else:
            return None
        return dt.isoformat()

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Daemon — background scheduler
# ═══════════════════════════════════════════════════════════════════════════════

_daemon_running = False
_daemon_thread: Optional[threading.Thread] = None


def _daemon_loop() -> None:
    """Background thread: periodically check for due tasks and fire them."""
    global _daemon_running
    while _daemon_running:
        due = store.get_due()
        for task in due:
            _fire_task(task)
        _sleep(POLL_INTERVAL)


def _fire_task(task: dict) -> None:
    """Fire a due task: POST a user message to the gateway /api/chat."""
    tid = task["id"]
    cid = task["conversation_id"]
    message = task.get("message", "Scheduled wake-up")
    when = task.get("when", "?")

    store.mark_fired(tid)

    wakeup_msg = f"[SCHEDULED WAKE-UP — {when}]\n{message}"

    payload = {
        "conversation_id": cid,
        "message": wakeup_msg,
        "conv_type": "scheduled_task",
    }

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(f"{GATEWAY_URL}/api/chat", json=payload)
            resp.raise_for_status()
        _log(f"fired task {tid} → conv {cid[:8]}  msg=%.60s", message)
    except httpx.HTTPError as exc:
        _log(f"FAILED to fire task {tid}: {exc}")


def _start_daemon() -> None:
    global _daemon_running, _daemon_thread
    if _daemon_running:
        return
    _daemon_running = True
    _daemon_thread = threading.Thread(target=_daemon_loop, daemon=True)
    _daemon_thread.start()
    _log("daemon started (poll=%ds, gateway=%s)", POLL_INTERVAL, GATEWAY_URL)


def _sleep(seconds: float) -> None:
    for _ in range(int(seconds * 2)):
        if not _daemon_running:
            break
        time.sleep(0.5)


# ═══════════════════════════════════════════════════════════════════════════════
# Main — stdio JSON-RPC event loop
# ═══════════════════════════════════════════════════════════════════════════════


def _log(fmt: str, *args) -> None:
    """Log to stderr (does not interfere with stdio protocol)."""
    msg = fmt % args if args else fmt
    sys.stderr.write(f"[{SERVER_NAME}] {msg}\n")
    sys.stderr.flush()


def main() -> None:
    _log("starting (pid=%d, data=%s)", os.getpid(), SCHEDULES_FILE)

    # Start the background scheduler *after* MCP handshake? Or immediately?
    # Immediately — the daemon is independent and the connection pool keeps us alive.
    _start_daemon()

    buffer = ""
    while True:
        try:
            line = sys.stdin.readline()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break

        buffer += line
        try:
            request = json.loads(buffer)
            buffer = ""
        except json.JSONDecodeError:
            continue  # partial read, wait for more

        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()

    global _daemon_running
    _daemon_running = False
    _log("shutting down")


if __name__ == "__main__":
    main()
