"""
Workspace operations for SWE‑Bench runner.

All git cloning happens on the HOST (which has internet).
Files are then transferred into worker containers via ``docker cp``.
Patches are extracted from containers via ``docker exec``.
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Git operations on host ────────────────────────────────────────────

def clone_and_squash(
    repo: str,
    base_commit: str,
    *,
    target_dir: Optional[Path] = None,
) -> Path:
    """
    Clone a GitHub repo on the host, checkout *base_commit*,
    then squash git history to a single commit.

    Args:
        repo:   GitHub repo slug, e.g. "django/django"
        base_commit: Full 40-char SHA to checkout.
        target_dir: Where to clone. If None, a temp dir is created.

    Returns:
        Path to the prepared repository directory.

    The resulting directory has exactly one git commit ("base") with
    all files from *base_commit*.  No prior history is preserved,
    preventing the agent from peeking at ``git log``.
    """
    url = f"https://github.com/{repo}.git"

    if target_dir is None:
        target_dir = Path(tempfile.mkdtemp(prefix="swe_clone_"))
    else:
        target_dir = Path(target_dir)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Cloning %s @ %s → %s", repo, base_commit[:8], target_dir)

    # Init empty repo, fetch only the specific commit (no branch assumptions)
    _run(["git", "-C", str(target_dir), "init"])
    _run(["git", "-C", str(target_dir), "remote", "add", "origin", url])
    _run(["git", "-C", str(target_dir), "fetch", "--depth", "1", "origin", base_commit])
    _run(["git", "-C", str(target_dir), "checkout", "FETCH_HEAD"])

    # Remove git history — keep only the checkout state
    git_dir = target_dir / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir)

    # Re-init with a single commit
    _run(["git", "-C", str(target_dir), "init"])
    _run(["git", "-C", str(target_dir), "add", "-A"])
    _run(
        ["git", "-C", str(target_dir), "commit", "--no-verify", "-m", "base"],
        env_add={"GIT_AUTHOR_NAME": "SWE-bench",
                 "GIT_AUTHOR_EMAIL": "swe@bench.local",
                 "GIT_COMMITTER_NAME": "SWE-bench",
                 "GIT_COMMITTER_EMAIL": "swe@bench.local",
                 },
    )

    file_count = sum(1 for _ in target_dir.rglob("*"))
    logger.info("Prepared %s (%d files)", target_dir, file_count)
    return target_dir


# ── Docker integration ────────────────────────────────────────────────

def copy_to_container(host_path: Path, container_name: str) -> None:
    """
    Copy the contents of *host_path* into the container's /workspace/.

    Uses ``docker cp <host>/. <container>:/workspace/``.
    """
    logger.info("docker cp %s → %s:/workspace/", host_path, container_name)
    _run(["docker", "cp", f"{host_path}/.", f"{container_name}:/workspace/"])


def extract_patch(container_name: str) -> str:
    """
    Run ``git diff HEAD`` inside the container's workspace.

    Returns the unified diff as a string (empty if no changes).
    """
    logger.info("Extracting patch from %s", container_name)
    result = subprocess.run(
        ["docker", "exec", container_name, "git", "-C", "/workspace", "diff", "HEAD"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        logger.warning("git diff failed in %s: %s", container_name, result.stderr)
    return result.stdout


def clear_workspace(container_name: str) -> None:
    """
    Remove all files from the container's /workspace/.

    Uses ``docker exec ... rm -rf /workspace/*``.
    """
    logger.debug("Clearing workspace in %s", container_name)
    _run(
        ["docker", "exec", container_name, "sh", "-c",
         "rm -rf /workspace/* /workspace/.[!.]* /workspace/..?*"],
        check=False,  # ok if already empty
    )


def container_has_git(container_name: str) -> bool:
    """Check whether 'git' is available inside the container."""
    result = subprocess.run(
        ["docker", "exec", container_name, "which", "git"],
        capture_output=True, text=True, timeout=5,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


# ── Helpers ───────────────────────────────────────────────────────────

def _run(cmd: list[str], check: bool = True, env_add: Optional[dict] = None) -> subprocess.CompletedProcess:
    """Run a subprocess, logging the command."""
    logger.debug("$ %s", " ".join(cmd))
    env = None
    if env_add:
        import os
        env = os.environ.copy()
        env.update(env_add)
    return subprocess.run(cmd, check=check, capture_output=True, text=True, timeout=120, env=env)


# ── Convenience: full prepare-for-agent pipeline ──────────────────────

def prepare_instance_workspace(
    instance: dict,
    container_name: str,
    *,
    clone_dir: Optional[Path] = None,
) -> Path:
    """
    Clone repo on host, squash git, docker cp into container.

    Args:
        instance: SWE‑bench instance dict (must have 'repo', 'base_commit').
        container_name: Docker container name.
        clone_dir: Optional fixed dir for clone (default: temp dir).

    Returns:
        Path to the host-side clone directory (caller should clean it up).
    """
    repo = instance["repo"]
    base_commit = instance["base_commit"]

    # 1. Clone + squash on host
    host_dir = clone_and_squash(repo, base_commit, target_dir=clone_dir)

    # 2. Clear container workspace
    clear_workspace(container_name)

    # 3. Copy into container
    copy_to_container(host_dir, container_name)

    return host_dir
