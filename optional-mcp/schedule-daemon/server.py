#!/usr/bin/env python3
"""
ScheduleWake — a prompt-only stdio MCP server for agent self-wakeup.

This server exposes **zero** function tools.  Its entire purpose is:

  1. During the MCP ``initialize`` handshake, return an ``instructions``
     string that teaches the agent how to schedule its own future wake-ups
     by writing files into ``.schedules/``.  ToolStore surfaces that
     ``instructions`` text to the agent's system prompt — that prompt IS
     the tool.

  2. Run a background daemon thread that watches ``.schedules/`` for
     due tasks and, when one matures, POSTs a user message to the
     AuroraCoder gateway's ``/api/chat`` endpoint, resuming the
     conversation as if the user had typed the message.

The agent interacts purely through the file system using tools it
already has (``write_file``, ``edit_file``, ``delete_file``,
``list_directory``, ``read_file``).  No tool calls are ever made into
this process.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

# ── Constants ─────────────────────────────────────────────────────────────────

SERVER_NAME = "ScheduleWake"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"

# How often the daemon thread re-scans schedules and checks for due tasks.
POLL_INTERVAL_SECONDS = 15

# Where to find the AuroraCoder gateway (resumes the conversation).
GATEWAY_URL = os.environ.get("CONVO_SERVER_URL", "http://localhost:8081")

# The workspace root whose ``.schedules/`` subdirectory we watch.  Defaults
# to the current working directory of the server process, which — when
# launched by ToolStore under AuroraCoder — is the agent workspace.
WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_DIR", os.getcwd())).resolve()
SCHEDULES_DIR = WORKSPACE_ROOT / ".schedules"

# Fired-schedule handling.  Default ("") → delete the file after firing,
# since a one-shot wake-up has no reason to linger.  Set SCHEDULE_ARCHIVE_DIR
# to a directory name (relative to the workspace) to move fired files there
# instead, e.g. ".schedules/.done".
ARCHIVE_DIR_NAME = os.environ.get("SCHEDULE_ARCHIVE_DIR", "")

# ── The onboarding prompt that ToolStore surfaces to the agent ───────────────

INSTRUCTIONS = """\
Schedule wake-ups for yourself

You can schedule a wake-up message that will be sent to this conversation
at a future time — resuming you exactly as if the user had typed it.  Use
this to check on long-running training, run periodic CI, follow up on a
process that has not finished, or pick something back up later.

Schedule files live in the `.schedules/` directory of the workspace.  Each
file is one schedule.  To create one, use your existing file tools — no
special tool call is needed:

    write_file(".schedules/<descriptive-name>.json", {
        "conversation_id": "<YOUR CURRENT CONVERSATION ID>",
        "when": "<when to wake up>",
        "message": "<what your future self should do>"
    })

Fields

- `conversation_id`  — your current conversation ID.  It is
  `{conversation_id}`.  Put that exact value here; the wake-up is
  delivered back to *this* conversation.
- `when`  — when the message should arrive.  Either an ISO 8601
  datetime (`2026-07-23T15:00:00Z`) or a relative time
  (`in 30 minutes`, `in 6 hours`, `in 2 days`).
- `message`  — the user message you will receive.  Write it the way you
  would want a user to phrase it; be specific about what to check or do,
  because the woken-you may not remember why you set this.

Manage your schedules with the file tools you already have

- New schedule      → `write_file(".schedules/check_loss.json", {...})`
- Change a schedule→ `edit_file(".schedules/check_loss.json", ...)`
- Cancel one       → `delete_file(".schedules/check_loss.json")`
- Inspect pending  → `list_directory(".schedules/")` then `read_file`

Lifecycle

A background watcher in this server scans `.schedules/` every
15s, fires any schedule whose `when` has arrived, then removes the
file.  Editing a schedule file re-reads it; deleting it cancels the
wake-up.  Nothing else is required of you.

Example

You just started a long training run and want to check on it in 6 hours:

    write_file(".schedules/check_training_loss.json", {
        "conversation_id": "{conversation_id}",
        "when": "in 6 hours",
        "message": "Check whether the training run in /workspace/runs/exp3 has converged. Read the latest log, and if the loss is still dropping, schedule another check 6 hours from now; if it has plateaued, stop the run and summarise the results."
    })

Keep the filename descriptive (no spaces), so that several schedules can
coexist without colliding.
"""


# ═══════════════════════════════════════════════════════════════════════════════
# Schedule daemon — file watcher + due-task firer
# ═══════════════════════════════════════════════════════════════════════════════


class ScheduleDaemon:
    """Watches ``.schedules/`` and fires due tasks.

    The file system is the single source of truth: the agent writes /
    edits / deletes files, and this daemon reconciles its in-memory index
    to the disk on every scan.

    In-memory index:  filename → (mtime, parsed task dict).  Only files
    that parse to a valid schedule (with the required keys) are tracked.
   """

    def __init__(self, schedules_dir: Path, poll_interval: float):
        self._dir = schedules_dir
        self._poll = poll_interval
        self._lock = threading.Lock()
        # filename → (mtime, task dict)
        self._index: Dict[str, Tuple[float, dict]] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        # Best-effort mkdir; if it fails we simply watch nothing until the
        # agent creates it.
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._thread = threading.Thread(
            target=self._loop, name="ScheduleWake-daemon", daemon=True
        )
        self._thread.start()
        log("daemon started (poll=%.0fs, dir=%s, gateway=%s)",
            self._poll, self._dir, GATEWAY_URL)

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        log("daemon stopped")

    # ── main loop ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                self._reconcile()
                self._fire_due()
            except Exception:
                log("daemon iteration error", exc_info=True)
            self._sleep(self._poll)

    def _sleep(self, seconds: float) -> None:
        """Sleep in small steps so stop() is responsive."""
        for _ in range(int(seconds * 2)):
            if not self._running:
                return
            time.sleep(0.5)

    # ── reconcile index with disk ───────────────────────────────────────

    def _reconcile(self) -> None:
        """Update the in-memory index to match what is on disk."""
        if not self._dir.exists():
            return

        seen: Dict[str, float] = {}
        for entry in self._dir.iterdir():
            if not entry.is_file() or not entry.name.endswith(".json"):
                continue
            # Don't pick up archive subfolder leak if ARCHIVE lives under .schedules.
            if entry.name.startswith("."):
                continue
            try:
                seen[entry.name] = entry.stat().st_mtime
            except FileNotFoundError:
                continue

        with self._lock:
            # New / modified files → load them.
            for name, mtime in seen.items():
                prev = self._index.get(name)
                if prev is None or mtime > prev[0]:
                    task = self._load(name)
                    if task is not None:
                        self._index[name] = (mtime, task)
                    else:
                        # Invalid: drop it so we don't keep retrying every cycle.
                        self._index.pop(name, None)
            # Deleted files → drop them.
            for name in list(self._index):
                if name not in seen:
                    del self._index[name]

    def _load(self, name: str) -> Optional[dict]:
        """Parse a schedule file, returning the task dict or None if invalid."""
        path = self._dir / name
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log("could not parse %s", name, exc_info=True)
            return None
        if not isinstance(raw, dict):
            return None
        missing = [k for k in ("conversation_id", "when", "message") if k not in raw]
        if missing:
            log("skipping %s: missing keys %s", name, missing)
            return None
        # Normalise `when` to an ISO 8601 string right away so the due-check
        # is cheap.  Store both the resolved and original forms.
        resolved = _resolve_when(raw["when"])
        if resolved is None:
            log("skipping %s: could not parse 'when'=%r", name, raw["when"])
            return None
        raw["when_resolved"] = resolved
        return raw

    # ── fire due tasks ───────────────────────────────────────────────────

    def _fire_due(self) -> None:
        now = datetime.now(timezone.utc)
        to_fire: List[str] = []
        with self._lock:
            for name, (_mtime, task) in self._index.items():
                try:
                    trigger = datetime.fromisoformat(task["when_resolved"])
                    if trigger.tzinfo is None:
                        trigger = trigger.replace(tzinfo=timezone.utc)
                except (ValueError, KeyError):
                    continue
                if trigger <= now:
                    to_fire.append(name)
        for name in to_fire:
            self._fire(name)

    def _fire(self, name: str) -> None:
        """Fire one schedule: POST to gateway, then delete / archive the file."""
        with self._lock:
            entry = self._index.pop(name, None)
        if entry is None:
            return
        _mtime, task = entry

        cid = task["conversation_id"]
        message = task.get("message", "Scheduled wake-up")
        when = task.get("when_resolved", task.get("when", "?"))

        wakeup_msg = f"[SCHEDULED WAKE-UP — {when}]\n{message}"
        payload = {
            "conversation_id": cid,
            "message": wakeup_msg,
            "conv_type": "scheduled_task",
        }

        ok = False
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(f"{GATEWAY_URL}/api/chat", json=payload)
                resp.raise_for_status()
            ok = True
            log("fired %s → conv %s  msg=%.60s", name, cid[:8], message)
        except httpx.HTTPError as exc:
            log("FAILED to fire %s: %s", name, exc)

        # Remove from disk regardless (don't retry a dud forever).
        path = self._dir / name
        try:
            if ARCHIVE_DIR_NAME and ok:
                archive = self._dir.parent / ARCHIVE_DIR_NAME if not Path(ARCHIVE_DIR_NAME).is_absolute() else Path(ARCHIVE_DIR_NAME)
                # under same parent (.schedules) by default; allow override
                if str(ARCHIVE_DIR_NAME).startswith(".schedules"):
                    archive = (WORKSPACE_ROOT / ARCHIVE_DIR_NAME)
                archive.mkdir(parents=True, exist_ok=True)
                target = archive / path.name
                path.replace(target)
                log("archived %s → %s", name, target)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            log("could not remove schedule file %s: %s", path, exc)


# ── helpers ──────────────────────────────────────────────────────────────────


def _resolve_when(raw: str) -> Optional[str]:
    """Parse `when` into an ISO 8601 UTC datetime string, or None.

    Accepts:
      - ISO 8601:      2026-07-23T08:00:00Z / 2026-07-23T08:00:00+00:00
      - Relative:      in 30 minutes, in 6 hours, in 2 days
    """
    if not isinstance(raw, str):
        return None
    raw = raw.strip()

    # Already ISO-ish?
    if re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", raw):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()

    # Relative: "in X (second|minute|hour|day)s"
    m = re.match(
        r"^in\s+(\d+)\s*(seconds?|minutes?|hours?|days?)$", raw, re.IGNORECASE
    )
    if m:
        value = int(m.group(1))
        unit = m.group(2).lower().rstrip("s")
        now = datetime.now(timezone.utc)
        kwargs = {
            "second": {"seconds": value},
            "minute": {"minutes": value},
            "hour": {"hours": value},
            "day": {"days": value},
        }[unit]
        dt = now + timedelta(**kwargs)
        return dt.isoformat()

    return None


def log(fmt: str, *args: Any) -> None:
    """Log to stderr (never stdout — stdout is the MCP channel)."""
    msg = fmt % args if args else fmt
    sys.stderr.write(f"[{SERVER_NAME}] {msg}\n")
    sys.stderr.flush()


# ═══════════════════════════════════════════════════════════════════════════════
# MCP stdio server (zero tools)
# ═══════════════════════════════════════════════════════════════════════════════


def handle_request(request: dict) -> Optional[dict]:
    """Process one JSON-RPC request; return a response dict or None (for notifications)."""
    method = request.get("method", "")
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        # This server intentionally exposes no tools. The agent drives
        # scheduling purely through the file system (write_file / edit_file /
        # delete_file) using the instructions above.
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": []}}

    if method == "tools/call":
        # No tools to call — return a clear "method not available" style error.
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": (
                    "ScheduleWake exposes no callable tools. Schedule wake-ups by "
                    "writing a JSON file to .schedules/ — see the server instructions."
                ),
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def main() -> None:
    log("starting (pid=%d, workspace=%s)", os.getpid(), WORKSPACE_ROOT)

    # Start the background daemon immediately — it is the entire point of
    # the server, runs independently of MCP traffic, and the ToolStore
    # connection pool keeps this process alive for its lifetime.
    daemon = ScheduleDaemon(SCHEDULES_DIR, POLL_INTERVAL_SECONDS)
    daemon.start()

    buffer = ""
    while True:
        try:
            line = sys.stdin.readline()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break  # stdin closed → parent is shutting us down

        buffer += line
        try:
            request = json.loads(buffer)
            buffer = ""
        except json.JSONDecodeError:
            continue  # partial line — wait for more

        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()

    daemon.stop()
    log("shutting down")


if __name__ == "__main__":
    main()