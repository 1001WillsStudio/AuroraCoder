"""
Workspace file-display utilities — file snapshots, diffs, tree building,
and workspace operations.  Used by the gateway's REST endpoints to serve
the frontend's file browser, diff viewer, and upload/download features.

The gateway resolves the workspace directory via ``src.config.WORKSPACE_DIR``.
"""

import logging
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Dict, Any, Optional, List

import difflib

from src.config import MAX_FILE_READ_SIZE

logger = logging.getLogger(__name__)

# ============================================================================
# File Diff — Snapshot-based tracking per conversation
# ============================================================================

# File snapshots per conversation: {conversation_id: {file_path: content}}
file_snapshots: Dict[str, Dict[str, str]] = {}

# Track which files have been touched (read or written) per conversation
files_touched: Dict[str, set] = {}


def snapshot_file(conversation_id: str, file_path: str, content: str):
    """Store a snapshot of a file's content at the start of a turn.

    Only stores if we don't already have a snapshot for this file in this
    conversation.
    """
    if conversation_id not in file_snapshots:
        file_snapshots[conversation_id] = {}

    if file_path not in file_snapshots[conversation_id]:
        file_snapshots[conversation_id][file_path] = content
        logger.debug(
            f"[snapshot] Saved snapshot for {file_path} ({len(content)} chars)"
        )


def mark_file_touched(conversation_id: str, file_path: str):
    """Mark a file as touched (read or written) in this conversation.

    Also invalidates the file-tree cache so the frontend picks up the change.
    """
    if conversation_id not in files_touched:
        files_touched[conversation_id] = set()
    files_touched[conversation_id].add(file_path)
    invalidate_tree_cache()


def clear_conversation_snapshots(conversation_id: str):
    """Clear snapshots for a conversation (new turn or clear chat)."""
    if conversation_id in file_snapshots:
        del file_snapshots[conversation_id]
    if conversation_id in files_touched:
        del files_touched[conversation_id]


def compute_unified_diff(original: str, current: str) -> list:
    """Compute a unified diff between *original* and *current* content.

    Returns a list of dicts with ``lineNumber``, ``content``, and
    ``type`` (``"added"``, ``"removed"``, or ``None``).
    """
    original_lines = original.split("\n") if original else []
    current_lines = current.split("\n") if current else []

    matcher = difflib.SequenceMatcher(None, original_lines, current_lines)
    opcodes = matcher.get_opcodes()

    result = []
    current_line_num = 1

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            for idx in range(j1, j2):
                result.append({
                    "lineNumber": current_line_num,
                    "content": current_lines[idx],
                    "type": None,
                })
                current_line_num += 1
        elif tag == "replace":
            for idx in range(i1, i2):
                result.append({
                    "lineNumber": None,
                    "content": original_lines[idx],
                    "type": "removed",
                })
            for idx in range(j1, j2):
                result.append({
                    "lineNumber": current_line_num,
                    "content": current_lines[idx],
                    "type": "added",
                })
                current_line_num += 1
        elif tag == "delete":
            for idx in range(i1, i2):
                result.append({
                    "lineNumber": None,
                    "content": original_lines[idx],
                    "type": "removed",
                })
        elif tag == "insert":
            for idx in range(j1, j2):
                result.append({
                    "lineNumber": current_line_num,
                    "content": current_lines[idx],
                    "type": "added",
                })
                current_line_num += 1

    return result



def get_file_diffs_for_conversation(
    conversation_id: str, work_dir: Path
) -> Dict[str, Any]:
    """Get diffs for all files touched in a conversation.

    Compares current file state against snapshots taken at turn start.
    """
    result: Dict[str, Any] = {"files": [], "error": None}

    if not work_dir or not work_dir.exists():
        result["error"] = "No active session"
        return result

    touched = files_touched.get(conversation_id, set())
    snapshots = file_snapshots.get(conversation_id, {})

    for file_path in touched:
        try:
            full_path = work_dir / file_path

            # Get current content — skip files too large to safely read.
            current_content = ""
            if full_path.exists() and full_path.is_file():
                try:
                    size = full_path.stat().st_size
                    if size > MAX_FILE_READ_SIZE:
                        logger.warning(
                            f"Skipping diff for {file_path} ({size:,} bytes — too large)"
                        )
                        continue
                    current_content = full_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                except Exception as e:
                    logger.warning(f"Could not read file {file_path}: {e}")
                    continue
            elif not full_path.exists():
                current_content = ""

            original_content = snapshots.get(file_path, "")
            if original_content == current_content:
                continue

            lines = compute_unified_diff(original_content, current_content)
            has_changes = any(
                line["type"] in ("added", "removed") for line in lines
            )

            if has_changes or not original_content:
                result["files"].append({
                    "id": file_path,
                    "path": file_path,
                    "lines": lines,
                    "hasChanges": has_changes,
                    "isNew": not original_content and bool(current_content),
                })
        except Exception as e:
            logger.error(f"Error processing file {file_path}: {e}")

    return result


# ============================================================================
# File Tree — with caching to avoid rebuilding on every request
# ============================================================================

# Enough levels to show a file six directories under an uploaded project
# (sample-project/a/b/c/d/e/f/deep.txt). The Workspace UI fetches this
# same cap; raise both together if a deeper tree is needed.
FILE_TREE_MAX_DEPTH = 32

# In-memory cache: rebuilt lazily, invalidated by file writes + a short TTL
# (the TTL catches file changes made by terminal commands that bypass
# mark_file_touched).
_tree_cache: Dict[str, Any] = {
    "tree": [],          # cached tree list
    "root": "",          # root path string (cache key)
    "timestamp": 0.0,    # time.time() when built
    "version": 0,        # monotonic counter (useful for ETag)
    "max_depth": None,   # depth the cached tree was built with
}
_tree_cache_ttl = 5.0     # seconds — safety net for terminal-created files
_files_changed = True      # start True to force initial build


def invalidate_tree_cache():
    """Invalidate the file-tree cache (called whenever a file write is detected)."""
    global _files_changed
    _files_changed = True


def get_cached_file_tree(
    directory: Path,
    base_path: Path,
    max_depth: int = FILE_TREE_MAX_DEPTH,
) -> tuple:
    """Return (tree, root_str, version) — with caching.

    Rebuilds the tree only when *directory* has changed (cached root
    differs), *max_depth* differs from the cached tree, the cache was
    explicitly invalidated, or the TTL expired.
    """
    global _tree_cache, _files_changed

    now = time.time()
    root_str = str(directory)
    cache_hit = (
        not _files_changed
        and _tree_cache["tree"]
        and _tree_cache["root"] == root_str
        and _tree_cache.get("max_depth") == max_depth
        and (now - _tree_cache["timestamp"]) < _tree_cache_ttl
    )

    if cache_hit:
        return _tree_cache["tree"], _tree_cache["root"], _tree_cache["version"]

    # Rebuild
    tree = build_file_tree(directory, base_path, max_depth)
    _tree_cache["tree"] = tree
    _tree_cache["root"] = root_str
    _tree_cache["timestamp"] = now
    _tree_cache["version"] += 1
    _tree_cache["max_depth"] = max_depth
    _files_changed = False

    return tree, root_str, _tree_cache["version"]

def build_file_tree(
    directory: Path,
    base_path: Path,
    max_depth: int = FILE_TREE_MAX_DEPTH,
    current_depth: int = 0,
) -> list:
    """Recursively build a file-tree structure for *directory*.

    Returns a list of dicts with ``name``, ``path``, ``type``
    (``"file"`` / ``"folder"``), ``children`` (folders), and
    ``extension`` (files).
    """
    if current_depth >= max_depth:
        return []

    items = []
    try:
        entries = sorted(
            directory.iterdir(),
            key=lambda e: (not e.is_dir(), e.name.lower()),
        )
    except PermissionError:
        return items
    except Exception as e:
        logger.warning(f"Error reading directory {directory}: {e}")
        return items

    SKIP_NAMES = {"__pycache__", "node_modules", ".git", ".venv", "venv"}

    for entry in entries:
        if entry.name.startswith(".") or entry.name in SKIP_NAMES:
            continue

        relative_path = str(entry.relative_to(base_path)).replace("\\", "/")

        if entry.is_dir():
            children = build_file_tree(
                entry, base_path, max_depth, current_depth + 1
            )
            items.append({
                "name": entry.name,
                "path": relative_path,
                "type": "folder",
                "children": children,
            })
        else:
            ext = entry.suffix.lower() if entry.suffix else ""
            items.append({
                "name": entry.name,
                "path": relative_path,
                "type": "file",
                "extension": ext,
            })

    return items


# ============================================================================
# Workspace helpers (upload / delete / export)
# ============================================================================

WORKSPACE_EXCLUDE = {
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".auroracoder_sessions",
    ".mypy_cache",
    ".pytest_cache",
}


def clear_workspace(work_dir: Path) -> None:
    """Remove everything from *work_dir* except excluded directories."""
    for child in list(work_dir.iterdir()):
        if child.name in WORKSPACE_EXCLUDE:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def count_workspace_files(work_dir: Path) -> int:
    """Count all files recursively under *work_dir*."""
    if not work_dir or not work_dir.exists():
        return 0
    return sum(1 for _ in work_dir.rglob("*") if _.is_file())


# ===========================================================================
# Workspace Tree (text) — compact text tree injected into the agent's system
# message so the agent has a basic understanding of the workspace without
# spending turns on manual exploration.
# ===========================================================================

_TREE_SKIP_NAMES = {
    "__pycache__", "node_modules", ".git", ".venv", "venv",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist",
    "build", ".next", "target",
}




def generate_workspace_tree_text(
    workspace_root: Path,
    max_depth: int = 3,
    max_files_per_dir: int = 2,
) -> str:
    """Generate a compact text tree of the workspace for the system message.

    Plain-indentation style (no emoji / box-drawing characters):

        workspace/\n          src/\n            app.py\n            utils.py\n          ... and 5 more file(s)

    - All directories up to *max_depth* are shown.
    - Up to *max_files_per_dir* files per directory, sorted by modification
      time (most recent first).  Excess files get an ``... and N more`` line.
    - Hidden items (dot-prefixed) and noise directories are skipped.
    - Empty directories (zero files after filtering) are omitted.
    - Single-child directory chains are collapsed into a single entry
      (e.g. ``a/b/`` instead of two nested levels when *a* contains
      only the subdirectory *b*).
    """
    SKIP = _TREE_SKIP_NAMES
    INDENT = "  "
    lines = [f"{workspace_root.name}/"]

    def _walk(path: Path, prefix: str, depth: int) -> bool:
        """Walk *path*, appending lines under *prefix*.

        Returns ``True`` when at least one entry was rendered (so the parent
        knows the directory is non-empty).
        """
        if depth > max_depth:
            return False
        try:
            entries = list(path.iterdir())
        except (PermissionError, OSError):
            return False

        visible = [
            e for e in entries
            if not e.name.startswith(".") and e.name not in SKIP
        ]
        dirs = sorted(
            [e for e in visible if e.is_dir()],
            key=lambda e: e.name.lower(),
        )
        files = sorted(
            [e for e in visible if e.is_file()],
            key=lambda e: (-_safe_mtime(e), e.name.lower()),
        )

        # --- Collapse single-child directory chains --------------------------
        collapsed = []
        while len(dirs) == 1 and len(files) == 0 and depth + len(collapsed) <= max_depth:
            solo = dirs[0]
            try:
                next_entries = list(solo.iterdir())
            except (PermissionError, OSError):
                return bool(collapsed)
            next_visible = [
                e for e in next_entries
                if not e.name.startswith(".") and e.name not in SKIP
            ]
            next_dirs = [e for e in next_visible if e.is_dir()]
            next_files = [e for e in next_visible if e.is_file()]

            if not next_dirs and not next_files:
                return bool(collapsed)  # chain ends empty → skip
            if len(next_dirs) == 1 and not next_files:
                # Still a single-dir chain — accumulate the name.
                collapsed.append(solo.name)
                path = solo
                dirs = next_dirs
                files = next_files
                continue
            # Chain ends here — capture the final directory's real state.
            collapsed.append(solo.name)
            path = solo
            dirs = sorted(next_dirs, key=lambda e: e.name.lower())
            files = sorted(
                next_files,
                key=lambda e: (-_safe_mtime(e), e.name.lower()),
            )
            break

        if collapsed:
            lines.append(f"{prefix}{'/'.join(collapsed)}/")
        # --------------------------------------------------------------------

        show_files = files[:max_files_per_dir]
        remaining = len(files) - max_files_per_dir

        all_entries = dirs + show_files
        total_shown = len(all_entries) + (1 if remaining > 0 else 0)

        if total_shown == 0:
            return False

        for entry in all_entries:
            if entry.is_dir():
                lines.append(f"{prefix}{entry.name}/")
                if depth < max_depth:
                    _walk(entry, prefix + INDENT, depth + 1)
            else:
                lines.append(f"{prefix}{entry.name}")

        if remaining > 0:
            lines.append(f"{prefix}... and {remaining} more file(s)")

        return True

    _walk(workspace_root, "", 0)
    return "\n".join(lines)


def _safe_mtime(entry: Path) -> float:
    """Return *entry*'s mtime, or 0 on any OS error."""
    try:
        return entry.stat().st_mtime
    except OSError:
        return 0.0


