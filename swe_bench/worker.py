"""
Worker: manages one AuroraCoder Docker container.

Each worker runs its own AuroraCoder stack (gateway + backend) in a
Docker container on the ``--internal`` bridge network.  It handles
one SWE‑bench instance at a time — clone repo → send to agent →
collect diff + conversation.
"""

import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from swe_bench.config import RunnerConfig
from swe_bench.gateway_client import GatewayClient
from swe_bench.workspace import (
    prepare_instance_workspace,
    extract_patch,
    clear_workspace,
)

logger = logging.getLogger(__name__)

# ── Docker subprocess helpers ─────────────────────────────────────────

def _docker(*args: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a docker CLI command."""
    cmd = ["docker", *args]
    logger.debug("$ %s", " ".join(cmd))
    return subprocess.run(cmd, check=check, capture_output=True, text=True, timeout=timeout)




# ── Worker ────────────────────────────────────────────────────────────

class Worker:
    """
    One AuroraCoder Docker container.

    Lifecycle:
        worker = Worker(0, config)
        await worker.start()          # docker run
        result = await worker.run_instance(instance_dict)
        await worker.stop()           # docker stop + rm
    """

    def __init__(self, worker_id: int, config: RunnerConfig):
        self.worker_id = worker_id
        self.config = config
        self.name = f"{config.container_prefix}-{worker_id}"
        self.gateway_host_port = config.gateway_port_base + worker_id
        self.frontend_host_port = config.frontend_port_base + worker_id
        self._ws_volume = f"{config.volume_prefix}_ws_{worker_id}"
        self._data_volume = f"{config.volume_prefix}_data_{worker_id}"
        self._running = False

    # ── Container lifecycle ─────────────────────────────────────────

    async def start(self) -> None:
        """
        Create named volumes + start the container.

        Retries up to 3 times if startup fails.
        """
        for attempt in range(1, 4):
            try:
                await self._start_once()
                self._running = True
                logger.info("Worker %d started (gw=%d, name=%s)",
                            self.worker_id, self.gateway_host_port, self.name)
                return
            except Exception as e:
                logger.warning("Worker %d start attempt %d failed: %s",
                               self.worker_id, attempt, e)
                if attempt == 3:
                    raise
                await self._cleanup()
                await asyncio.sleep(2 ** attempt)

    async def _start_once(self) -> None:
        """Single start attempt."""
        # Create volumes if they don't exist
        for vol in (self._ws_volume, self._data_volume):
            try:
                _docker("volume", "create", vol, check=False)
            except Exception:
                pass  # volume may already exist

        # Build port mapping args
        port_args = [
            "-p", f"{self.gateway_host_port}:{self.config.gateway_port}",
        ]
        if self.config.frontend_enabled:
            port_args.extend([
                "-p", f"{self.frontend_host_port}:{self.config.frontend_port}",
            ])

        # Environment
        env = os.environ.copy()
        env_vars = {
            "WORKSPACE_DIR": "/workspace",
            "AURORACODER_DATA_DIR": "/swe_data",
            "BACKEND_PORT": str(self.config.backend_port),
            "GATEWAY_PORT": str(self.config.gateway_port),
        }
        if "ACCESS_PASSWORD" in env:
            env_vars["ACCESS_PASSWORD"] = env["ACCESS_PASSWORD"]

        env_args: list[str] = []
        for k, v in env_vars.items():
            env_args.extend(["-e", f"{k}={v}"])

        # docker run
        cmd = [
            "docker", "run", "-d",
            "--name", self.name,
            "--network", self.config.network,
            *port_args,
            *env_args,
            "-v", f"{self._ws_volume}:/workspace",
            "-v", f"{self._data_volume}:/swe_data",
            self.config.docker_image,
        ]
        logger.debug("$ %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"docker run failed: {result.stderr.strip()}")

        # Wait for gateway health
        await self._wait_healthy(timeout=60)

    async def _wait_healthy(self, timeout: float = 60) -> None:
        """Poll /health until 200 or timeout."""
        client = GatewayClient(self.gateway_url, timeout=5.0)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await client.health():
                return
            await asyncio.sleep(1.0)
        raise RuntimeError(f"Gateway on {self.gateway_url} did not become healthy within {timeout}s")

    async def stop(self) -> None:
        """Stop and remove the container and its volumes."""
        if not self._running:
            return
        await self._cleanup()
        self._running = False

    async def _cleanup(self) -> None:
        """Force-remove container + volumes (best-effort)."""
        for args in (
            (f"docker", "stop", self.name),
            (f"docker", "rm", "-f", self.name),
            (f"docker", "volume", "rm", "-f", self._ws_volume),
            (f"docker", "volume", "rm", "-f", self._data_volume),
        ):
            try:
                logger.debug("$ %s", " ".join(args))
                subprocess.run(args, capture_output=True, timeout=10)
            except Exception:
                pass

    # ── Instance runner ──────────────────────────────────────────────

    async def run_instance(self, instance: dict) -> dict:
        """
        Full lifecycle for one SWE‑bench instance.

        1. Clone repo on host, squash git, docker cp → /workspace
        2. Send problem statement → consume SSE
        3. Extract patch via docker exec git diff
        4. Fetch conversation via gateway API
        5. Save everything to swe_runs/{id}/

        Returns a result dict with keys:
            instance_id, status, patch_len, elapsed, error
        """
        instance_id = instance.get("instance_id", "unknown")
        t0 = time.monotonic()
        result = {
            "instance_id": instance_id,
            "status": "unknown",
            "patch_len": 0,
            "elapsed": 0.0,
            "error": None,
        }

        logger.info("[w%d] Starting %s", self.worker_id, instance_id)

        try:
            # 1. Prepare workspace
            problem = instance.get("problem_statement", instance.get("problem", ""))
            if not problem:
                raise ValueError(f"Instance {instance_id} has no problem_statement")

            host_clone_dir = None
            try:
                host_clone_dir = prepare_instance_workspace(
                    instance, self.name,
                )
            finally:
                # Clean up host-side clone directory
                if host_clone_dir and host_clone_dir.exists():
                    import shutil
                    shutil.rmtree(host_clone_dir, ignore_errors=True)

            # 2. Send to agent
            client = GatewayClient(self.gateway_url, timeout=self.config.instance_timeout)
            done = await client.wait_for_done(
                conversation_id=instance_id,
                message=problem,
                provider=self.config.provider,
                timeout=self.config.instance_timeout,
            )
            result["status"] = done.status

            # 3. Extract patch
            patch = extract_patch(self.name)
            result["patch_len"] = len(patch)

            # 4. Fetch conversation
            conv_json = await client.get_conversation(instance_id)

            # 5. Save
            run_dir = self.config.runs_path / instance_id
            run_dir.mkdir(parents=True, exist_ok=True)

            (run_dir / "patch.diff").write_text(patch, encoding="utf-8")
            (run_dir / "conversation.json").write_text(
                json.dumps(conv_json, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            elapsed = time.monotonic() - t0
            result["elapsed"] = round(elapsed, 1)
            (run_dir / "metadata.json").write_text(
                json.dumps({
                    "instance_id": instance_id,
                    "worker_id": self.worker_id,
                    "status": result["status"],
                    "patch_len": result["patch_len"],
                    "elapsed": elapsed,
                    "provider": self.config.provider,
                }, indent=2),
                encoding="utf-8",
            )

            logger.info("[w%d] %s → %s (patch=%d bytes, %.1fs)",
                        self.worker_id, instance_id, result["status"],
                        result["patch_len"], elapsed)

        except Exception as e:
            elapsed = time.monotonic() - t0
            result["status"] = "error"
            result["error"] = str(e)
            result["elapsed"] = round(elapsed, 1)
            logger.error("[w%d] %s FAILED: %s", self.worker_id, instance_id, e)

            # Still try to save any partial conversation
            try:
                client = GatewayClient(self.gateway_url, timeout=10.0)
                conv_json = await client.get_conversation(instance_id)
                run_dir = self.config.runs_path / instance_id
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "conversation.json").write_text(
                    json.dumps(conv_json, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception:
                pass

            # Save error metadata
            try:
                run_dir = self.config.runs_path / instance_id
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "metadata.json").write_text(
                    json.dumps(result, indent=2), encoding="utf-8")
            except Exception:
                pass

        finally:
            # Always clear workspace for next instance
            try:
                clear_workspace(self.name)
            except Exception:
                pass

        return result

    # ── Properties ───────────────────────────────────────────────────

    @property
    def gateway_url(self) -> str:
        return f"http://localhost:{self.gateway_host_port}"

    @property
    def frontend_url(self) -> Optional[str]:
        if not self.config.frontend_enabled:
            return None
        return f"http://localhost:{self.frontend_host_port}"
