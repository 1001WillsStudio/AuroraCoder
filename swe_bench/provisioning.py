"""
Gold-standard per-instance environment provisioning.

SWE-bench instances are pinned to the Python version + dependency set that
existed at their ``base_commit``. A single shared Python 3.12 ``agent`` env
cannot build/run most of them, so for each instance we create a dedicated
conda env (default name ``testbed``) with the correct Python and the repo's
dependencies installed — exactly mirroring the official SWE-bench harness.

Design notes
------------
* The official spec data lives in the ``swebench`` package
  (``MAP_REPO_VERSION_TO_SPECS`` + ``make_env_script_list_py``). We *generate*
  the bash setup commands on the host (which has internet, so the embedded
  ``requirements.txt`` / ``environment.yml`` are fetched and inlined), then run
  them inside the worker container.
* ``swebench.harness`` imports the Unix-only ``resource`` module at package
  import time. The host runner may be Windows, so we stub it before importing.
* The official scripts assume conda at ``/opt/miniconda3`` and re-clone the
  repo into ``/testbed``. Our container has conda at ``/opt/conda`` and the
  squashed repo already lives in ``/workspace`` — so we swap the conda prefix
  and build the repo-install portion ourselves against ``/workspace`` (no
  re-clone, preserving our git hygiene).
"""

from __future__ import annotations

import logging
import subprocess
import sys
import types
from typing import Optional

logger = logging.getLogger(__name__)

# Container paths (continuumio/miniconda3 base image).
CONDA_PREFIX = "/opt/conda"
REPO_DIR = "/workspace"


# ── swebench import (with Windows `resource` stub) ─────────────────────

def _ensure_swebench():
    """Import swebench spec helpers, stubbing the Unix-only ``resource`` module.

    Returns a tuple of the pieces we need, or raises ImportError if swebench is
    not installed.
    """
    if "resource" not in sys.modules:
        try:
            import resource  # noqa: F401
        except ImportError:
            stub = types.ModuleType("resource")
            stub.RLIMIT_NOFILE = 0
            stub.getrlimit = lambda _res: (1024, 1024)
            stub.setrlimit = lambda *_a, **_k: None
            sys.modules["resource"] = stub

    from swebench.harness.constants import (
        MAP_REPO_VERSION_TO_SPECS,
        MAP_REPO_TO_INSTALL,
    )
    from swebench.harness.test_spec.python import (
        make_env_script_list_py,
        get_test_directives,
    )
    return (
        MAP_REPO_VERSION_TO_SPECS,
        MAP_REPO_TO_INSTALL,
        make_env_script_list_py,
        get_test_directives,
    )


# ── Public API ─────────────────────────────────────────────────────────

def get_specs(instance: dict):
    """Return the (repo, version) spec dict, or None if unknown."""
    try:
        maps = _ensure_swebench()
    except ImportError:
        logger.warning("swebench not installed — cannot provision env. "
                       "pip install swebench")
        return None
    MAP_REPO_VERSION_TO_SPECS = maps[0]
    repo = instance.get("repo")
    version = str(instance.get("version", ""))
    try:
        return MAP_REPO_VERSION_TO_SPECS[repo][version]
    except KeyError:
        logger.warning("No SWE-bench spec for %s @ version %r — skipping provision",
                       repo, version)
        return None


def build_setup_script(
    instance: dict,
    *,
    env_name: str = "testbed",
    editable_install: bool = True,
) -> Optional[str]:
    """Build the full bash provisioning script for *instance*.

    Returns None when no spec is available (caller should skip provisioning).
    """
    maps = _ensure_swebench()
    (MAP_REPO_VERSION_TO_SPECS, MAP_REPO_TO_INSTALL,
     make_env_script_list_py, _get_test_directives) = maps

    specs = get_specs(instance)
    if specs is None:
        return None

    repo = instance["repo"]

    # 1. Environment creation commands (conda env + python + deps). swebench
    #    fetches and inlines requirements.txt / environment.yml here.
    env_cmds = make_env_script_list_py(instance, specs, env_name)

    # 2. Repo install commands — run against our existing /workspace checkout.
    install_cmds = [
        f"source {CONDA_PREFIX}/bin/activate",
        f"conda activate {env_name}",
        'echo "Provision env: $CONDA_DEFAULT_ENV"',
        f"cd {REPO_DIR}",
        f"git config --global --add safe.directory {REPO_DIR}",
    ]
    if repo in MAP_REPO_TO_INSTALL:
        install_cmds.append(MAP_REPO_TO_INSTALL[repo])
    for pre in specs.get("pre_install", []) or []:
        install_cmds.append(pre)
    if "install" in specs:
        install_cmds.append(_maybe_editable(specs["install"], editable_install))

    # 3. Absorb any setup-induced file changes into the base commit so the
    #    agent's final `git diff HEAD` only reflects the agent's own edits.
    commit_cmds = [
        "git config --global user.email setup@swebench.config",
        "git config --global user.name SWE-bench",
        "git add -A",
        'git commit --allow-empty -m "swe-bench provision" || true',
    ]

    # Workers are reused across instances, so a stale env of the same name may
    # exist. Remove it first — `conda create` errors if the env already exists.
    cleanup_cmds = [
        f"source {CONDA_PREFIX}/bin/activate",
        f"conda env remove -n {env_name} -y 2>/dev/null || true",
    ]

    all_cmds = cleanup_cmds + env_cmds + install_cmds + commit_cmds

    # Adapt official conda prefix to our image.
    script_body = "\n".join(all_cmds).replace("/opt/miniconda3", CONDA_PREFIX)

    return "#!/bin/bash\nset -euxo pipefail\n" + script_body + "\n"


def get_test_command(instance: dict) -> Optional[str]:
    """Return the full test command (test_cmd + directives) for the instance."""
    specs = get_specs(instance)
    if specs is None:
        return None
    maps = _ensure_swebench()
    get_test_directives = maps[3]
    try:
        directives = get_test_directives(instance)
    except Exception:
        directives = []
    return " ".join([specs.get("test_cmd", "pytest"), *directives]).strip()


def _maybe_editable(install_cmd: str, editable: bool) -> str:
    """Convert a local-project ``pip install .`` to editable so the agent's
    edits to /workspace are reflected without reinstalling. Left unchanged for
    any other install form."""
    if not editable:
        return install_cmd
    stripped = install_cmd.strip()
    if stripped.endswith("pip install ."):
        return stripped[:-1] + "-e ."
    return install_cmd


# ── Container execution ────────────────────────────────────────────────

def provision_container(
    container_name: str,
    script: str,
    *,
    timeout: int = 1800,
) -> tuple[bool, str]:
    """Write *script* into the container and execute it.

    Returns (success, combined_output_tail).
    """
    # Write the script via stdin to avoid shell-quoting issues. Send raw bytes
    # with LF endings — text mode on Windows would translate \n -> \r\n and the
    # trailing \r breaks bash (e.g. `set -euxo pipefail\r`).
    script_bytes = script.replace("\r\n", "\n").encode("utf-8")
    write = subprocess.run(
        ["docker", "exec", "-i", container_name, "bash", "-c",
         "cat > /tmp/provision.sh && chmod +x /tmp/provision.sh"],
        input=script_bytes, capture_output=True, timeout=60,
    )
    if write.returncode != 0:
        return False, f"failed to write provision script: {write.stderr.decode(errors='replace')}"

    logger.info("Provisioning env in %s (timeout=%ds)...", container_name, timeout)
    try:
        run = subprocess.run(
            ["docker", "exec", container_name, "bash", "/tmp/provision.sh"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"provisioning timed out after {timeout}s"

    output = (run.stdout or "") + (run.stderr or "")
    tail = output[-3000:]
    if run.returncode != 0:
        logger.error("Provisioning failed in %s (exit %d)", container_name, run.returncode)
        return False, tail
    logger.info("Provisioning succeeded in %s", container_name)
    return True, tail
