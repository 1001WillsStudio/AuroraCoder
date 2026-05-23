"""
Workspace file-display utilities — file snapshots, diffs, tree building,
and workspace operations.  Used by the gateway's REST endpoints to serve
the frontend's file browser, diff viewer, and upload/download features.

The gateway resolves the workspace directory via ``src.config.WORKSPACE_DIR``.
"""

import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, Any, Optional, List

import difflib

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
    """Mark a file as touched (read or written) in this conversation."""
    if conversation_id not in files_touched:
        files_touched[conversation_id] = set()
    files_touched[conversation_id].add(file_path)


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

            # Get current content
            if full_path.exists() and full_path.is_file():
                try:
                    current_content = full_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                except Exception as e:
                    logger.warning(f"Could not read file {file_path}: {e}")
                    continue
            else:
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
# File Tree
# ============================================================================

def build_file_tree(
    directory: Path,
    base_path: Path,
    max_depth: int = 5,
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
    ".thinktool_sessions",
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
