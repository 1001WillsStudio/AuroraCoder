"""
SWE-Bench Runner configuration.

All defaults can be overridden via swe_bench_config.yaml or CLI flags.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class RunnerConfig:
    # ── Dataset ──────────────────────────────────────────────────
    dataset_path: str = "data/swe-bench.jsonl"
    """Path to SWE‑bench JSONL dataset."""

    max_instances: int = 0
    """Max instances to run (0 = all)."""

    # ── Parallelism ──────────────────────────────────────────────
    workers: int = 4
    """Number of concurrent AuroraCoder containers."""

    # ── Docker ───────────────────────────────────────────────────
    docker_image: str = "auroracoder:latest"
    """Docker image for each worker container."""

    container_prefix: str = "auroracoder-swe"
    """Prefix for worker container names → auroracoder-swe-0, …"""

    network: str = "swe-net"
    """Docker bridge network name (created with --internal)."""

    volume_prefix: str = "swe"
    """Prefix for named volumes → swe_ws_0, swe_data_0."""

    gateway_port_base: int = 8081
    """Host port for worker 0's gateway; worker i = base + i."""

    backend_port: int = 8080
    """Port the backend listens on INSIDE each container."""

    gateway_port: int = 8081
    """Port the gateway listens on INSIDE each container."""

    frontend_port: int = 3000
    """Port the frontend listens on INSIDE each container."""

    frontend_enabled: bool = False
    """Map frontend ports to host for live debugging."""

    frontend_port_base: int = 3001
    """Host port for worker 0's frontend; worker i = base + i."""

    # ── Agent ────────────────────────────────────────────────────
    provider: str = "deepseek"
    """LLM provider passed to the gateway."""

    instance_timeout: float = 1800.0
    """Seconds before cancelling a stuck instance (30 min default)."""

    # ── Output ───────────────────────────────────────────────────
    runs_dir: str = "swe_runs"
    """Directory where per‑instance results are saved."""

    # ── Derived ──────────────────────────────────────────────────
    @property
    def runs_path(self) -> Path:
        return Path(self.runs_dir).resolve()


def load_config(config_path: Optional[str] = None, cli_overrides: Optional[dict] = None) -> RunnerConfig:
    """
    Load config from YAML file, then apply CLI overrides.

    Priority (highest last):
        1. dataclass defaults
        2. swe_bench_config.yaml
        3. CLI flags (cli_overrides dict)
    """
    import yaml

    cfg = RunnerConfig()

    # Layer 2: YAML file
    yaml_path = Path(config_path) if config_path else Path("swe_bench_config.yaml")
    if yaml_path.exists():
        with open(yaml_path) as f:
            yaml_data = yaml.safe_load(f) or {}
        for key, value in yaml_data.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)

    # Layer 3: CLI overrides
    if cli_overrides:
        for key, value in cli_overrides.items():
            if value is not None and hasattr(cfg, key):
                setattr(cfg, key, value)

    return cfg
