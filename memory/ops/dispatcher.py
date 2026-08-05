"""
Memory-worker dispatcher — Layer 2b (Gap Engine heavy ops), scaffolding.

**Disabled by default and inert unless explicitly turned on** via both
``settings.other.memory.enabled`` (master switch) and
``settings.other.memory.heavy_ops_enabled`` (this layer specifically —
see ``memory/settings.py``). This module intentionally
does two different things with two different confidence levels:

1. Container lifecycle (spawn / snapshot / teardown) — real,
   unit-tested (with docker/filesystem calls mocked out; this repo's
   working agreement is to never invoke real docker from an automated
   session). Uses **Docker-in-Docker**: the gateway process shells out to
   the host's ``docker`` CLI via the mounted Docker socket to launch a
   sibling ``auroracoder`` container in a slim ``memory-worker`` role
   (see ``docker/entrypoint.sh`` and ``docker/supervisord.memory-worker.conf``).
   The worker gets an isolated **copy** of the current workspace, never
   the live one, per the design doc's isolation requirement for
   tool-using ops the user isn't watching.

2. The actual investigation protocol: drive the worker's backend through
   a one-shot "investigate with tools, then report findings" task over
   its bare ``POST /api/chat`` (the worker has no gateway of its own —
   see ``src/web_api/app.py``), then hand the resulting transcript to
   ``memory/ops/extractor.py``'s ``run_gap_investigation_extraction`` —
   the SAME judged write pass a normal session goes through, treating
   the worker's ``report_findings`` tool call as a nomination exactly
   like `remember` (see that module's docstring "Layer 2b reuses this
   same gate"). ``investigate_gap_via_worker`` below owns the HTTP/SSE
   side; it does no parsing of its own, it only returns whatever final
   transcript it managed to get.

## Reaching the worker: shared user-defined network, not published ports

The main container and any spawned worker are **sibling containers** on
the host's Docker daemon (DooD, via the mounted socket) — they are NOT
nested, so ``localhost`` from inside the main container never reaches a
worker, no matter what port the worker publishes. Two things had to be
true for this to work at all, both prerequisites outside this module:

1. The main container needs the Docker socket mounted
   (``launcher/docker.go``, gated on ``heavy_ops_enabled`` being on
   *before* the container is created — mounts can't be added to an
   already-running container) so it can even run ``docker`` commands.
2. Both containers need to share a **user-defined** bridge network —
   Docker's embedded DNS (container-name → IP resolution) only works on
   user-defined networks, never on the default ``bridge`` network. See
   ``ensure_memory_network()``: it creates ``MEMORY_NETWORK_NAME`` and
   self-connects the currently-running main container to it (a running
   container CAN be attached to an additional network after the fact,
   unlike a socket mount). The worker just gets ``--network
   MEMORY_NETWORK_NAME`` directly in its ``docker run`` invocation.

With both on the same user-defined network, the worker is reachable at
``worker_base_url(gap_id)`` (its container name, resolved by Docker's
DNS) — no port publishing, no ``docker port`` discovery step needed.

See docs/code-agent-memory-design.md §13 (Gap Engine) for the target
behavior this is scaffolding toward.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from gateway.settings_store import get_other_settings
from memory.gap_store import get_gap_ledger
from memory.settings import memory_enabled, heavy_ops_enabled
from memory.ops.judge_io import EXTRACTION_PLAN_TOOL, CONSOLIDATION_PLAN_TOOL
from memory.ops.prompts import GAP_INVESTIGATION_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

WORKER_ROLE = "memory-worker"
DEFAULT_WORKER_IMAGE = "auroracoder"
CONTAINER_NAME_PREFIX = "auroracoder-memory-worker-"
WORKER_PORT = 8080

# How many agent-loop iterations the worker gets for one investigation —
# generous enough for a handful of read_file/grep/git log calls, capped so
# a confused investigation can't spin forever.
INVESTIGATION_MAX_ITERATIONS = 20

# Per-HTTP-call timeouts (connect, read-per-chunk) and an overall wall-clock
# budget for the whole SSE exchange — belt-and-suspenders: a single stalled
# read is caught by the read timeout, a slow-but-still-trickling stream is
# caught by the wall-clock deadline.
WORKER_CONNECT_TIMEOUT = 10
WORKER_READ_TIMEOUT = 120
INVESTIGATION_WALL_CLOCK_BUDGET = 600

# How long to wait for a freshly-spawned worker's FastAPI app to come up
# before giving up on this gap for now (it gets re-investigated next pass).
WORKER_READY_TIMEOUT = 30
WORKER_READY_POLL_INTERVAL = 1.0

# User-defined bridge network shared by the main container and every
# memory-worker it spawns — see module docstring "Reaching the worker".
# Fixed name, single source of truth: both ends (this module, run every
# time a worker is spawned) agree on it without needing any discovery.
MEMORY_NETWORK_NAME = "auroracoder-memory-net"

# Matches launcher/docker.go's `containerName` constant — the name the
# main container is always started with. Passed explicitly via
# AURORACODER_CONTAINER_NAME (set by the launcher alongside the socket
# mount) so this module never has to guess its own container identity;
# the literal fallback only matters for older images predating that env
# var, where the name is this hard-coded constant anyway.
DEFAULT_CONTAINER_NAME = "auroracoder-agent"


def _worker_image() -> str:
    mem = get_other_settings().get("memory", {})
    return mem.get("worker_image", "") or DEFAULT_WORKER_IMAGE


def _snapshot_root() -> Path:
    from memory.store import DEFAULT_STORAGE_DIR
    root = DEFAULT_STORAGE_DIR / "gap_workspaces"
    root.mkdir(parents=True, exist_ok=True)
    return root


def snapshot_workspace(gap_id: str) -> Path:
    """Copy the live workspace into an isolated scratch dir for the worker.

    Never hands the worker container the live workspace — an
    investigation task should never be able to mutate what the user is
    actively looking at. Copy failures propagate (caller must not spawn
    a worker without a valid, isolated snapshot).
    """
    from src.code_sandbox import WORKSPACE

    dest = _snapshot_root() / gap_id
    if dest.exists():
        shutil.rmtree(dest)
    if WORKSPACE.exists():
        shutil.copytree(WORKSPACE, dest, dirs_exist_ok=True)
    else:
        dest.mkdir(parents=True, exist_ok=True)
    return dest


def cleanup_snapshot(gap_id: str) -> None:
    dest = _snapshot_root() / gap_id
    shutil.rmtree(dest, ignore_errors=True)


# Provider API keys, kept in sync with src/config.py's MODEL_PROVIDERS (each
# entry there reads its "api_key" from exactly one of these env vars). The
# main container gets these from the launcher's --env-file at startup (see
# launcher/docker.go); a freshly-spawned worker starts with a bare
# environment and no settings.json of its own (deliberate isolation — see
# module docstring), so without forwarding these explicitly the worker could
# never reach any provider at all and every investigation would silently
# dead-end on "not configured". Passed as -e values straight through to the
# child process, never written to a file on either side.
PROVIDER_API_KEY_ENV_VARS = ("DEEPSEEK_API_KEY", "OPENCODE_API_KEY", "NVIDIA_API_KEY")


def _provider_env_passthrough() -> List[str]:
    args: List[str] = []
    for name in PROVIDER_API_KEY_ENV_VARS:
        value = os.environ.get(name)
        if value:
            args += ["-e", f"{name}={value}"]
    return args


def build_docker_run_args(gap_id: str, snapshot_dir: Path) -> List[str]:
    """Construct the ``docker run`` argv for a one-off memory-worker.

    Conventions mirror ``launcher/docker.go`` (image name, ``-e
    AURORACODER_DOCKER=1``, ``/workspace`` mount) so this container is
    indistinguishable from a normal AuroraCoder container from the
    backend's point of view — only ``AURORACODER_ROLE`` differs, which
    ``docker/entrypoint.sh`` uses to boot the slim supervisord profile
    instead of the full desktop/gateway/frontend stack.

    No ``-p`` port publishing — the worker is reached over
    ``MEMORY_NETWORK_NAME`` by container name (see module docstring),
    which also means it's never exposed on the host at all.
    """
    container_name = f"{CONTAINER_NAME_PREFIX}{gap_id}"
    return [
        "run", "--rm", "-d",
        "--name", container_name,
        "--network", MEMORY_NETWORK_NAME,
        "-e", "AURORACODER_DOCKER=1",
        "-e", f"AURORACODER_ROLE={WORKER_ROLE}",
        *_provider_env_passthrough(),
        "-v", f"{snapshot_dir}:/workspace",
        _worker_image(),
    ]


def worker_base_url(gap_id: str) -> str:
    """Base URL for the worker spawned for *gap_id*, reachable from the
    main container once both are on ``MEMORY_NETWORK_NAME`` (Docker's
    embedded DNS resolves the container name to its IP on that network).
    """
    return f"http://{CONTAINER_NAME_PREFIX}{gap_id}:{WORKER_PORT}"


def _own_container_name() -> str:
    return os.environ.get("AURORACODER_CONTAINER_NAME", DEFAULT_CONTAINER_NAME)


def _run_docker(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60)


def _run_docker_tolerating_already_done(args: List[str], label: str) -> bool:
    """Run a docker command where "it already happened" (network already
    exists, container already connected) is a success, not a failure —
    both this call and a previous one racing/retrying against the same
    idempotent state should end up equally happy. Never raises."""
    try:
        result = _run_docker(args)
    except (OSError, subprocess.SubprocessError) as e:
        logger.error("[memory-worker] Failed to invoke docker for %s: %s", label, e)
        return False
    if result.returncode != 0 and "already exists" not in result.stderr and "already connected" not in result.stderr:
        logger.error("[memory-worker] %s failed: %s", label, result.stderr.strip())
        return False
    return True


def ensure_memory_network() -> bool:
    """Idempotently create ``MEMORY_NETWORK_NAME`` and attach the
    currently-running main container to it.

    Must be called before spawning any worker — see module docstring
    "Reaching the worker" for why a shared user-defined network (not
    published ports) is what makes sibling-container communication work
    at all. Fail-open like the rest of this module: returns False rather
    than raising, so a network setup failure just becomes another reason
    ``spawn_worker`` reports "failed to spawn" instead of crashing.
    """
    if not _run_docker_tolerating_already_done(["network", "create", MEMORY_NETWORK_NAME], "network create"):
        return False
    if not _run_docker_tolerating_already_done(
        ["network", "connect", MEMORY_NETWORK_NAME, _own_container_name()], "network connect (self)"
    ):
        return False
    return True


def spawn_worker(gap_id: str, snapshot_dir: Path) -> Optional[str]:
    """Launch the worker container. Returns the container name, or None on failure."""
    if not ensure_memory_network():
        logger.error("[memory-worker] Aborting spawn for gap %s — could not set up %s", gap_id, MEMORY_NETWORK_NAME)
        return None

    args = build_docker_run_args(gap_id, snapshot_dir)
    container_name = f"{CONTAINER_NAME_PREFIX}{gap_id}"
    try:
        result = _run_docker(args)
    except (OSError, subprocess.SubprocessError) as e:
        logger.error("[memory-worker] Failed to invoke docker for gap %s: %s", gap_id, e)
        return None
    if result.returncode != 0:
        logger.error("[memory-worker] docker run failed for gap %s: %s", gap_id, result.stderr.strip())
        return None
    return container_name


def teardown_worker(container_name: str) -> None:
    try:
        subprocess.run(["docker", "stop", container_name], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("[memory-worker] Failed to stop %s: %s", container_name, e)


def _wait_for_worker_ready(base_url: str, timeout: float = WORKER_READY_TIMEOUT) -> bool:
    """Poll the freshly-spawned worker's health endpoint until its FastAPI
    app (uvicorn, started by ``docker/supervisord.memory-worker.conf``) is
    actually accepting connections, or *timeout* elapses.

    A container reaching "running" state (``spawn_worker`` returning a
    name) says nothing about whether the process inside has finished
    booting yet — without this, the first ``/api/chat`` call would race
    the app's startup and likely fail with a connection error.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = requests.get(f"{base_url}/api/health", timeout=3)
            if resp.status_code == 200:
                return True
        except (requests.RequestException, OSError):
            pass
        time.sleep(WORKER_READY_POLL_INTERVAL)
    return False


def investigate_gap_via_worker(
    base_url: str,
    question: str,
    max_iterations: int = INVESTIGATION_MAX_ITERATIONS,
) -> Optional[List[Dict[str, Any]]]:
    """Drive a one-shot "investigate, then report_findings" task on an
    already-running, already-ready memory-worker over its bare
    ``POST /api/chat`` SSE endpoint.

    Returns the final raw message transcript — whatever the stream gave
    us, even from a non-clean terminal status (``max_iterations_reached``
    or a mid-stream error) — so the caller can still hand it to
    ``memory/ops/extractor.run_gap_investigation_extraction`` in case
    ``report_findings`` was already called on an earlier turn before
    things went wrong. Returns ``None`` only when we got NOTHING usable
    at all: the request itself failed, the worker responded with a
    non-200, or the stream produced not a single message snapshot.

    This function does no parsing of ``report_findings`` itself — see
    ``memory/ops/extractor.py``'s module docstring for why that judgment
    logic lives in exactly one place shared with the normal write pass.
    """
    body = {
        "messages": [{"role": "system", "content": GAP_INVESTIGATION_SYSTEM_PROMPT}],
        "message": (
            "Investigate this and call `report_findings` when you're done "
            f"(exactly once, as your last action):\n\n{question}"
        ),
        "tools": "gap_investigation",
        "max_iterations": max_iterations,
    }

    try:
        resp = requests.post(
            f"{base_url}/api/chat", json=body, stream=True,
            timeout=(WORKER_CONNECT_TIMEOUT, WORKER_READ_TIMEOUT),
        )
    except (requests.RequestException, OSError) as e:
        logger.error("[memory-worker] Could not reach worker at %s: %s", base_url, e)
        return None

    if resp.status_code != 200:
        logger.error(
            "[memory-worker] Worker at %s returned %s: %s",
            base_url, resp.status_code, resp.text[:500],
        )
        resp.close()
        return None

    raw_messages: Optional[List[Dict[str, Any]]] = None
    deadline = time.monotonic() + INVESTIGATION_WALL_CLOCK_BUDGET
    try:
        for line in resp.iter_lines(decode_unicode=True):
            if time.monotonic() > deadline:
                logger.warning(
                    "[memory-worker] Investigation at %s exceeded its %ss wall-clock budget — "
                    "using whatever transcript we have so far.",
                    base_url, INVESTIGATION_WALL_CLOCK_BUDGET,
                )
                break
            if not line or not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if "raw_messages" in data:
                raw_messages = data["raw_messages"]
    except (requests.RequestException, OSError) as e:
        logger.warning("[memory-worker] Stream from %s interrupted: %s", base_url, e)
    finally:
        resp.close()

    return raw_messages


def dispatch_gap_investigation(gap_id: str) -> Dict[str, Any]:
    """Entry point for actively investigating one open gap.

    Fail-open and inert by default: returns immediately without touching
    docker/filesystem unless ``heavy_ops_enabled()`` is true. When
    enabled: spawns an isolated worker on a snapshot of the workspace,
    waits for it to come up, drives it through the investigate/report
    protocol, and hands whatever transcript comes back to the shared
    write pass (``run_gap_investigation_extraction``). The gap is
    resolved only if that pass actually wrote/updated a memory; any other
    outcome (spawn failure, worker never came up, HTTP failure, the
    investigator reporting "could not resolve", or the write pass itself
    rejecting the finding) defers the gap instead of guessing. Never
    raises — every failure path is caught, logged, and turned into a
    deferral so a broken worker can never leave a gap stuck in
    "investigating" forever.
    """
    ledger = get_gap_ledger()
    gap = ledger.get(gap_id)
    if gap is None:
        return {"ok": False, "reason": "gap not found"}

    if not memory_enabled():
        return {"ok": False, "reason": "memory disabled (settings.other.memory.enabled)"}
    if not heavy_ops_enabled():
        return {"ok": False, "reason": "heavy_ops disabled (settings.other.memory.heavy_ops_enabled)"}

    container_name = None
    try:
        ledger.set_status(gap_id, "investigating")
        snapshot_dir = snapshot_workspace(gap_id)
        container_name = spawn_worker(gap_id, snapshot_dir)
        if container_name is None:
            ledger.defer(gap_id)
            return {"ok": False, "reason": "failed to spawn memory-worker container"}

        base_url = worker_base_url(gap_id)
        if not _wait_for_worker_ready(base_url):
            logger.error("[memory-worker] %s never became ready for gap %s", container_name, gap_id)
            ledger.defer(gap_id)
            return {"ok": False, "reason": "worker did not become ready in time", "container": container_name}

        raw_messages = investigate_gap_via_worker(base_url, gap["question"])
        if raw_messages is None:
            ledger.defer(gap_id)
            return {"ok": False, "reason": "investigation produced no usable transcript", "container": container_name}

        # Same judged write pass a normal session goes through — see
        # memory/ops/extractor.py's module docstring "Layer 2b reuses this
        # same gate". Rejects just as readily as it accepts; that's by
        # design, not a bug to work around here.
        from memory.ops.extractor import run_gap_investigation_extraction
        result = run_gap_investigation_extraction(gap_id, raw_messages)
        if result is None:
            ledger.defer(gap_id)
            return {"ok": False, "reason": "no resolved finding survived judgment", "container": container_name}

        memory_id, confidence = result
        ledger.resolve(gap_id, resolved_memory_id=memory_id, confidence=confidence)
        return {"ok": True, "gap_id": gap_id, "memory_id": memory_id, "confidence": confidence}
    except Exception:
        logger.exception("[memory-worker] Dispatch failed for gap %s", gap_id)
        ledger.defer(gap_id)
        return {"ok": False, "reason": "internal error, see logs"}
    finally:
        if container_name:
            teardown_worker(container_name)
        cleanup_snapshot(gap_id)


# ===========================================================================
# Memory maintenance (Layer 2b): extraction / consolidation through a real
# AuroraCoder worker conversation — reusable + observable. The gap-engine
# path above is a one-shot investigate+report task; this path is the same
# architecture applied to the two regular memory-maintenance tasks. See
# docs/code-agent-memory-design.md §19 (Design Doc) for why those should
# run in a background worker / sidecar — this is that.
# ===========================================================================

# Maintenance tasks are lighter: fewer iterations + shorter wall-clock.
MAINTENANCE_MAX_ITERATIONS = 15
MAINTENANCE_WALL_CLOCK_BUDGET = 300


def _maintenance_task_id(kind: str) -> str:
    return f"maint-{kind}-{uuid.uuid4().hex[:10]}"


def _build_maintenance_prompt(kind: str, payload: Dict[str, Any]):
    """Return (system_prompt, user_message, emit_tool_name) for the worker."""
    now_iso = payload.get("now_iso")
    if kind == "extraction":
        from memory.ops.prompts import EXTRACTION_SYSTEM_PROMPT, build_extraction_user_prompt
        user = build_extraction_user_prompt(
            payload.get("transcript", ""),
            nominated=payload.get("nominated"),
            existing_corpus=payload.get("corpus"),
            other_conversations_by_nomination=payload.get("other_convs"),
            session_meta=payload.get("session_meta"),
            now_iso=now_iso,
        )
        return EXTRACTION_SYSTEM_PROMPT, user, EXTRACTION_PLAN_TOOL
    else:  # consolidation
        from memory.ops.consolidator import CONSOLIDATION_SYSTEM_PROMPT
        corpus = payload.get("corpus", [])
        now_line = f"Current time (UTC): {now_iso}\n\n" if now_iso else ""
        user = (
            now_line
            + "Here is the COMPLETE current world-plane memory corpus. Decide which entries should be "
            "MERGED (a loser folded into a keeper) or DELETED (retired). Be conservative; when in doubt keep.\n"
            "--- WORLD MEMORY CORPUS START ---\n"
            + json.dumps(corpus, indent=2)
            + "\n--- WORLD MEMORY CORPUS END ---"
        )
        return CONSOLIDATION_SYSTEM_PROMPT, user, CONSOLIDATION_PLAN_TOOL


def _run_maintenance_via_worker(
    base_url: str,
    kind: str,
    payload: Dict[str, Any],
    max_iterations: int = MAINTENANCE_MAX_ITERATIONS,
) -> Optional[List[Dict[str, Any]]]:
    """Drive a one-shot maintenance task on the worker — mirror of
    ``investigate_gap_via_worker``. Returns the raw transcript, or None
    on total failure (no usable response at all)."""
    system, user, _emit_tool = _build_maintenance_prompt(kind, payload)
    body = {
        "messages": [{"role": "system", "content": system}],
        "message": user,
        "tools": "memory_maintenance",
        "max_iterations": max_iterations,
    }
    try:
        resp = requests.post(
            f"{base_url}/api/chat", json=body, stream=True,
            timeout=(WORKER_CONNECT_TIMEOUT, WORKER_READ_TIMEOUT),
        )
    except (requests.RequestException, OSError) as e:
        logger.error("[memory-worker] Could not reach worker at %s: %s", base_url, e)
        return None
    if resp.status_code != 200:
        logger.error(
            "[memory-worker] Worker at %s returned %s: %s",
            base_url, resp.status_code, resp.text[:500],
        )
        resp.close()
        return None
    raw_messages: Optional[List[Dict[str, Any]]] = None
    deadline = time.monotonic() + MAINTENANCE_WALL_CLOCK_BUDGET
    try:
        for line in resp.iter_lines(decode_unicode=True):
            if time.monotonic() > deadline:
                logger.warning(
                    "[memory-worker] Maintenance at %s exceeded its %ss wall-clock budget",
                    base_url, MAINTENANCE_WALL_CLOCK_BUDGET,
                )
                break
            if not line or not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if "raw_messages" in data:
                raw_messages = data["raw_messages"]
    except (requests.RequestException, OSError) as e:
        logger.warning("[memory-worker] Maintenance stream from %s interrupted: %s", base_url, e)
    finally:
        resp.close()
    return raw_messages


def extract_emitted_plan(
    raw_messages: Optional[List[Dict[str, Any]]], tool_name: str
) -> Optional[Dict[str, Any]]:
    """Pull the maintenance plan out of the transcript by finding the *emit*
    tool call named *tool_name* and parsing its arguments. Returns the
    LAST such plan (the final answer wins) or None. Never raises."""
    from memory.ops.judge_io import extract_json
    if not raw_messages:
        return None
    plan = None
    for msg in raw_messages:
        if not isinstance(msg, dict):
            continue
        for tc in (msg.get("tool_calls") or []):
            fn = (tc or {}).get("function") or {}
            if fn.get("name") != tool_name:
                continue
            args = fn.get("arguments")
            if isinstance(args, dict):
                plan = args
            elif isinstance(args, str):
                parsed = extract_json(args)
                if parsed is not None:
                    plan = parsed
    return plan


def _persist_maintenance_trace(
    kind: str,
    maint_id: str,
    conversation_id: str,
    request_messages: List[Dict[str, Any]],
    raw_messages: Optional[List[Dict[str, Any]]],
    status: str,
) -> bool:
    """Persist the full worker trace (request + transcript) into the MAIN
    conversation store under a labeled conversation_id so the trace is
    observable in the existing conversation system. Fail-open — an
    observability failure must never block the actual memory write."""
    try:
        from gateway.conversation_store import store
        store.create_conversation(conversation_id=conversation_id, conv_type="memory_maintenance")
        # Prepend a short user message so the auto-derived title is readable
        # (the real user message is a large corpus dump / transcript that
        # would become an illegible title). The worker request is unchanged.
        store_messages = [
            {"role": "user", "content": f"Memory maintenance: {kind} ({maint_id[:8]})"},
        ]
        store_messages.extend(request_messages)
        store_messages.extend(raw_messages or [])
        store.save_messages(conversation_id, store_messages)
        store.update_status(conversation_id, status)
        return True
    except Exception:
        logger.exception("[memory-worker] Failed to persist maintenance trace")
        return False


def _apply_maintenance_plan(
    kind: str, plan: Dict[str, Any], conversation_id: str
) -> Dict[str, Any]:
    """Apply the emitted plan on the main side. Extraction: upsert candidates
    via ``apply_extraction_plan``. Consolidation: apply merges/deletes via
    ``consolidator._apply_plan``."""
    if kind == "extraction":
        from memory.ops.extractor import apply_extraction_plan
        candidates = (plan or {}).get("memories", []) or []
        if not candidates:
            return {"applied": True, "written": [], "merged": 0, "deleted": 0}
        written = apply_extraction_plan(candidates, conversation_id, nomination_label="memory-maintenance")
        return {"applied": True, "written": written, "merged": 0, "deleted": 0}
    else:
        from memory.ops import consolidator
        from memory.store import get_repository
        repo = get_repository()
        result = consolidator._apply_plan(repo, plan or {"merges": [], "deletes": []})
        return {
            "applied": True,
            "written": [],
            "merged": result.get("merged", 0),
            "deleted": result.get("deleted", 0),
        }


def dispatch_memory_maintenance(
    kind: str, payload: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Orchestrate one memory-maintenance task (extraction or consolidation)
    through an isolated, on-demand AuroraCoder worker container so the trace
    is observable in the existing conversation system.

    Fail-open and inert by default: returns immediately without touching
    docker/filesystem unless both ``memory_enabled()`` AND
    ``heavy_ops_enabled()`` are true. Never raises — every failure path is
    caught, logged, and turned into a non-ok result.

    *kind* is ``"extraction"`` or ``"consolidation"``. *payload* carries the
    inputs the worker needs (transcript/nominated/corpus/etc.). For a
    self-contained consolidation pass, pass an empty dict — the caller just
    provides the corpus inline (reads it from the memory repo beforehand).
    """
    payload = payload or {}
    if kind not in ("extraction", "consolidation"):
        return {"ok": False, "reason": f"unknown maintenance kind: {kind!r}"}
    if not memory_enabled():
        return {"ok": False, "reason": "memory disabled (settings.other.memory.enabled)"}
    if not heavy_ops_enabled():
        return {"ok": False, "reason": "heavy_ops disabled (settings.other.memory.heavy_ops_enabled)"}

    maint_id = _maintenance_task_id(kind)
    conversation_id = f"memory-maintenance:{kind}:{maint_id}"
    container_name = None
    try:
        system, user, emit_tool = _build_maintenance_prompt(kind, payload)
        request_messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        snapshot_dir = snapshot_workspace(maint_id)
        container_name = spawn_worker(maint_id, snapshot_dir)
        if container_name is None:
            _persist_maintenance_trace(kind, maint_id, conversation_id, request_messages, [], "failed")
            return {"ok": False, "reason": "failed to spawn memory-worker container",
                    "conversation_id": conversation_id}

        base_url = worker_base_url(maint_id)
        if not _wait_for_worker_ready(base_url):
            _persist_maintenance_trace(kind, maint_id, conversation_id, request_messages, [], "failed")
            return {"ok": False, "reason": "worker did not become ready in time",
                    "container": container_name, "conversation_id": conversation_id}

        raw_messages = _run_maintenance_via_worker(base_url, kind, payload)
        if raw_messages is None:
            _persist_maintenance_trace(kind, maint_id, conversation_id, request_messages, [], "failed")
            return {"ok": False, "reason": "maintenance produced no usable transcript",
                    "container": container_name, "conversation_id": conversation_id}

        # Persist FIRST (observability is the point of this path), then
        # extract + apply the emitted plan.
        _persist_maintenance_trace(kind, maint_id, conversation_id, request_messages, raw_messages, "completed")
        plan = extract_emitted_plan(raw_messages, emit_tool)
        if plan is None:
            return {"ok": False, "reason": "worker emitted no maintenance plan",
                    "container": container_name, "conversation_id": conversation_id}
        applied = _apply_maintenance_plan(kind, plan, conversation_id)
        return {"ok": True, "kind": kind, "conversation_id": conversation_id,
                "container": container_name, "plan": plan, **applied}
    except Exception:
        logger.exception("[memory-worker] Maintenance dispatch failed (kind=%s)", kind)
        return {"ok": False, "reason": "internal error, see logs", "conversation_id": conversation_id}
    finally:
        if container_name:
            teardown_worker(container_name)
        cleanup_snapshot(maint_id)
