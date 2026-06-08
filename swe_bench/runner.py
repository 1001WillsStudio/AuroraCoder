"""
SWE‑Bench batch runner — pool of N isolated AuroraCoder Docker containers.

Usage:
    python -m swe_bench.runner \
        --dataset data/swe-bench.jsonl \
        --workers 4 \
        --provider deepseek \
        --max 100 \
        --frontend-enabled
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from swe_bench.config import RunnerConfig, load_config
from swe_bench.worker import Worker

logger = logging.getLogger("swe_bench.runner")


# ── .env loading ───────────────────────────────────────────────────────

def load_dotenv(path: str = ".env") -> None:
    """Best-effort load of a project ``.env`` into ``os.environ``.

    Minimal parser (no external dependency). Existing environment variables
    take priority and are never overwritten. Forwarded to worker containers
    so the agent can authenticate to LLM providers (see swe_bench.worker).
    """
    env_path = Path(path)
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception as e:  # pragma: no cover - best effort
        logger.debug("Could not load %s: %s", path, e)


# ── CLI ────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="SWE‑Bench batch runner — N parallel AuroraCoder workers",
    )
    p.add_argument("--config", default=None, help="YAML config file path")
    p.add_argument("--dataset", default=None, help="Path to SWE‑bench JSONL")
    p.add_argument("--max", dest="max_instances", type=int, default=None, help="Max instances to run")
    p.add_argument("--workers", type=int, default=None, help="Number of parallel workers")
    p.add_argument("--provider", default=None, help="LLM provider")
    p.add_argument("--docker-image", default=None, help="AuroraCoder Docker image")
    p.add_argument("--network", default=None, help="Docker network name")
    p.add_argument("--frontend-enabled", action="store_true", default=None, help="Expose frontend ports")
    p.add_argument("--timeout", dest="instance_timeout", type=float, default=None, help="Per-instance timeout (seconds)")
    p.add_argument("--max-iterations", dest="agent_max_iterations", type=int, default=None, help="Per-turn agent iteration cap inside the container")
    p.add_argument("--no-provision", dest="gold_standard_env", action="store_false", default=None, help="Disable gold-standard per-instance env provisioning")
    p.add_argument("--runs-dir", default=None, help="Output directory")
    p.add_argument("--gateway-port-base", type=int, default=None, help="First host port for gateways")
    p.add_argument("--verbose", "-v", action="store_true", default=False, help="Debug logging")
    return p.parse_args()


def args_to_overrides(args: argparse.Namespace) -> dict:
    """Convert CLI args to a dict of non‑None overrides for load_config()."""
    overrides = {}
    for key in (
        "dataset_path", "max_instances", "workers", "provider",
        "docker_image", "network", "instance_timeout", "runs_dir",
        "gateway_port_base", "agent_max_iterations",
    ):
        cli_val = getattr(args, key, None)
        if cli_val is not None:
            overrides[key] = cli_val
    # dataset → dataset_path
    if args.dataset is not None:
        overrides["dataset_path"] = args.dataset
    # frontend-enabled (only override if explicitly passed)
    if args.frontend_enabled is not None:
        overrides["frontend_enabled"] = True
    # gold-standard env provisioning (only override when --no-provision passed)
    if args.gold_standard_env is False:
        overrides["gold_standard_env"] = False
    # timeout
    if args.instance_timeout is not None:
        overrides["instance_timeout"] = args.instance_timeout
    return overrides


# ── Dataset loading ────────────────────────────────────────────────────

def load_dataset(path: str) -> list[dict]:
    """Load SWE‑bench instances from a JSONL file."""
    instances = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("instance_id"):
                    instances.append(obj)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed JSONL line")
    return instances


# ── Docker network ─────────────────────────────────────────────────────

def ensure_network(name: str) -> None:
    """Create the --internal Docker network if it doesn't exist."""
    result = subprocess.run(
        ["docker", "network", "inspect", name],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        logger.info("Docker network '%s' already exists", name)
        return

    logger.info("Creating --internal Docker network '%s'", name)
    subprocess.run(
        ["docker", "network", "create", "--internal", name],
        check=True,
    )



# ── Port conflict check ────────────────────────────────────────────────

def check_ports_free(base: int, count: int) -> list[int]:
    """
    Check that gateway ports base..base+count-1 are free.
    If frontend, also check frontend_port_base..frontend_port_base+count-1.
    Returns list of occupied ports (empty = all free).

    Uses a cross-platform socket bind probe (works on Windows, macOS, Linux)
    rather than shelling out to ``ss`` (Linux-only).
    """
    import socket

    occupied = []
    for i in range(count):
        port = base + i
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                occupied.append(port)
    return occupied


# ── Summary ────────────────────────────────────────────────────────────

def print_summary(results: list[dict], elapsed: float) -> None:
    """Print a summary table of all results."""
    print("\n" + "=" * 70)
    print(f"SWE‑Bench run complete — {len(results)} instances, {elapsed:.0f}s total")
    print("=" * 70)

    by_status: dict[str, int] = {}
    total_patch_bytes = 0
    for r in results:
        status = r.get("status", "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        total_patch_bytes += r.get("patch_len", 0)

    print(f"\nStatus breakdown:")
    for status, count in sorted(by_status.items()):
        print(f"  {status:25s} {count:4d}")
    print(f"\nTotal patch bytes: {total_patch_bytes:,}")
    print(f"Runs directory:     {Path('swe_runs').resolve()}")


# ── Main ───────────────────────────────────────────────────────────────

async def main_async(config: RunnerConfig) -> None:
    """Run the SWE‑bench batch."""

    # 1. Load dataset
    instances = load_dataset(config.dataset_path)
    if not instances:
        logger.error("No instances found in %s", config.dataset_path)
        return
    if config.max_instances > 0:
        instances = instances[:config.max_instances]
    logger.info("Loaded %d instances from %s", len(instances), config.dataset_path)

    # 2. Check ports
    occupied = check_ports_free(config.gateway_port_base, config.workers)
    if occupied:
        logger.error("Gateway ports already in use: %s", occupied)
        logger.error("Stop existing workers or use --gateway-port-base")
        return

    if config.frontend_enabled:
        occupied_fe = check_ports_free(config.frontend_port_base, config.workers)
        if occupied_fe:
            logger.error("Frontend ports already in use: %s", occupied_fe)
            return

    # 3. Ensure network
    ensure_network(config.network)

    # 4. Create workers + instance queue
    queue: asyncio.Queue[dict] = asyncio.Queue()
    for inst in instances:
        queue.put_nowait(inst)

    workers = [Worker(i, config) for i in range(config.workers)]

    # 5. Start all workers
    logger.info("Starting %d workers...", config.workers)
    start_tasks = [w.start() for w in workers]
    try:
        await asyncio.gather(*start_tasks)
    except Exception as e:
        logger.error("Failed to start workers: %s", e)
        # Best-effort cleanup
        for w in workers:
            try:
                await w.stop()
            except Exception:
                pass
        return

    logger.info("All %d workers started", config.workers)

    if config.frontend_enabled:
        print("\nFrontend URLs (open to watch agent live):")
        for w in workers:
            print(f"  Worker {w.worker_id}: {w.frontend_url}")
        print()

    # 6. Run instances
    results: list[dict] = []
    results_lock = asyncio.Lock()

    async def worker_loop(w: Worker) -> None:
        nonlocal results
        while True:
            try:
                inst = queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            result = await w.run_instance(inst)
            async with results_lock:
                results.append(result)

            remaining = queue.qsize()
            done = len(results)
            total = done + remaining
            print(f"[{done:4d}/{total}] {result['instance_id']:40s} "
                  f"{result['status']:15s} "
                  f"({result.get('elapsed', 0):.0f}s, "
                  f"patch={result.get('patch_len', 0)}b) "
                  f"w={w.worker_id}")

    t_start = time.monotonic()
    await asyncio.gather(*(worker_loop(w) for w in workers))
    t_end = time.monotonic()

    # 7. Stop workers
    logger.info("Stopping workers...")
    await asyncio.gather(*(w.stop() for w in workers))

    # 8. Print summary
    print_summary(results, t_end - t_start)


def main() -> None:
    """Entry point."""
    args = parse_args()

    # Set up logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load .env so provider API keys are available to forward to workers
    load_dotenv()

    # Load config (YAML + CLI overrides)
    config = load_config(args.config, args_to_overrides(args))

    # Handle SIGINT / SIGTERM gracefully
    loop = asyncio.new_event_loop()
    stop_requested = False

    def _signal_handler():
        nonlocal stop_requested
        if stop_requested:
            logger.warning("Force exit")
            sys.exit(1)
        stop_requested = True
        logger.warning("Graceful shutdown requested — finishing current instances...")
        # Cancel all tasks to trigger cleanup
        for task in asyncio.all_tasks(loop):
            task.cancel()

    # add_signal_handler is not implemented on Windows asyncio loops, and some
    # signals (e.g. SIGTERM) may be absent on certain platforms. Register
    # best-effort and fall back to the default KeyboardInterrupt handling.
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except (NotImplementedError, RuntimeError, ValueError):
            logger.debug("Signal handler for %s not supported on this platform", sig_name)

    try:
        loop.run_until_complete(main_async(config))
    except KeyboardInterrupt:
        logger.warning("Interrupted")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
