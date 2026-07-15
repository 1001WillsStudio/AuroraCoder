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

2. The actual investigation protocol (drive the worker's backend through
   a self-investigation task, parse structured findings, resolve the
   gap) — **not implemented yet**. ``dispatch_gap_investigation`` spawns
   the worker and immediately tears it down / defers the gap with a
   clear log message. Wiring up a real one-shot "investigate and report
   findings" tool + parsing contract against ``src/web_api`` is future
   work; this module exists so that work has somewhere to go without
   redesigning the container lifecycle plumbing.

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

import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from gateway.settings_store import get_other_settings
from memory.gap_store import get_gap_ledger
from memory.settings import memory_enabled, heavy_ops_enabled

logger = logging.getLogger(__name__)

WORKER_ROLE = "memory-worker"
DEFAULT_WORKER_IMAGE = "auroracoder"
CONTAINER_NAME_PREFIX = "auroracoder-memory-worker-"
WORKER_PORT = 8080

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


def dispatch_gap_investigation(gap_id: str) -> Dict[str, Any]:
    """Entry point for actively investigating one open gap.

    Fail-open and inert by default: returns immediately without touching
    docker/filesystem unless ``heavy_ops_enabled()`` is true. When
    enabled, spawns an isolated worker, then — since the investigation
    protocol itself isn't implemented yet (see module docstring) — tears
    it back down and defers the gap rather than pretending to resolve
    it. Never raises.
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

        logger.warning(
            "[memory-worker] Spawned %s for gap %s, but the investigation protocol "
            "is not implemented yet — deferring gap instead of investigating. "
            "See memory/ops/dispatcher.py module docstring.",
            container_name, gap_id,
        )
        ledger.defer(gap_id)
        return {"ok": False, "reason": "investigation protocol not implemented (scaffolding only)", "container": container_name}
    except Exception:
        logger.exception("[memory-worker] Dispatch failed for gap %s", gap_id)
        ledger.defer(gap_id)
        return {"ok": False, "reason": "internal error, see logs"}
    finally:
        if container_name:
            teardown_worker(container_name)
        cleanup_snapshot(gap_id)
