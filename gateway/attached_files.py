"""Per-turn workspace file attachments for chat.

The file tree lets a human *view* a file; this module is the missing loop
that lets them *point the agent at it*.  The frontend sends path chips;
the gateway validates them against the workspace and prefixes the user
message with a marked block so the model sees an explicit "read these
first" instruction.  The markers are stripped from the transcript the
same way ``[TASK INSTRUCTION]`` is — the bubble shows chips, not markup.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

ATTACHED_FILES_START = "[ATTACHED FILES]"
ATTACHED_FILES_END = "[/ATTACHED FILES]"
ATTACHED_FILES_INSTRUCTION = (
    "Read each of the following workspace files with read_file before acting:"
)
MAX_ATTACHED_FILES = 8

_ATTACHED_BLOCK_RE = re.compile(
    re.escape(ATTACHED_FILES_START)
    + r"(.*?)"
    + re.escape(ATTACHED_FILES_END)
    + r"\s*",
    re.DOTALL,
)


def strip_attached_files(content: str) -> str:
    """Remove an attached-files marker block from *content*."""
    if not isinstance(content, str) or ATTACHED_FILES_START not in content:
        return content if isinstance(content, str) else ""
    return _ATTACHED_BLOCK_RE.sub("", content).strip()


def extract_attached_files(content: str) -> Optional[List[str]]:
    """Return the path list inside an attached-files block, or None."""
    if not isinstance(content, str) or ATTACHED_FILES_START not in content:
        return None
    match = _ATTACHED_BLOCK_RE.search(content)
    if not match:
        return None
    paths = _paths_from_inner(match.group(1))
    return paths or None


def format_attached_files_block(paths: Sequence[str]) -> str:
    """Build the marked block the model sees. Empty *paths* → empty string."""
    clean = [p.strip() for p in paths if isinstance(p, str) and p.strip()]
    if not clean:
        return ""
    lines = [ATTACHED_FILES_START, ATTACHED_FILES_INSTRUCTION, *clean, ATTACHED_FILES_END]
    return "\n".join(lines)


def apply_attached_files(message: str, paths: Sequence[str]) -> str:
    """Insert (or replace) an attached-files block on a user message.

    Sits after a task-instruction wrapper when both are present, so standing
    orders stay first, this-turn files second, and the typed ask last.
    """
    text = message if isinstance(message, str) else ""
    text = strip_attached_files(text)
    block = format_attached_files_block(paths)
    if not block:
        return text
    task_end = "[/TASK INSTRUCTION]"

    if task_end in text:
        idx = text.find(task_end) + len(task_end)
        head = text[:idx].rstrip()
        tail = text[idx:].lstrip()
        if tail:
            return f"{head}\n\n{block}\n\n{tail}"
        return f"{head}\n\n{block}"
    if text:
        return f"{block}\n\n{text}"
    return block


def normalize_attached_paths(
    paths: Iterable[str],
    workspace: Optional[Path],
    *,
    limit: int = MAX_ATTACHED_FILES,
) -> List[str]:
    """Keep unique in-workspace files; drop traversal, dirs, and missing paths."""
    if workspace is None:
        return []
    try:
        root = Path(workspace).resolve()
    except OSError:
        return []
    if not root.exists() or not root.is_dir():
        return []

    seen: set[str] = set()
    out: List[str] = []
    for raw in paths or []:
        if len(out) >= limit:
            break
        if not isinstance(raw, str):
            continue
        candidate = raw.strip().replace("\\", "/")
        if not candidate or candidate in seen:
            continue
        rel = _safe_workspace_file(candidate, root)
        if rel is None or rel in seen:
            continue
        seen.add(rel)
        out.append(rel)
    return out


def prepare_attached_message(
    message: str,
    requested_paths: Optional[Sequence[str]],
    workspace: Optional[Path],
) -> tuple[str, List[str]]:
    """Validate paths and return ``(message_for_model, kept_paths)``.

    Paths may arrive as a structured list *or* already inside a marked
    block (the frontend wraps so a reload still shows chips).  Invalid
    paths are dropped; the block is rewritten from the survivors.
    """
    text = message if isinstance(message, str) else ""
    from_block = extract_attached_files(text) or []
    requested = [p for p in (requested_paths or []) if isinstance(p, str)]
    # Structured list wins when both are present (it is the live chip state).
    combined = requested if requested else from_block
    kept = normalize_attached_paths(combined, workspace)
    return apply_attached_files(text, kept), kept


def _paths_from_inner(inner: str) -> List[str]:
    paths: List[str] = []
    seen: set[str] = set()
    for line in inner.splitlines():
        item = line.strip().lstrip("- ").strip()
        if not item or item == ATTACHED_FILES_INSTRUCTION:
            continue
        if item in seen:
            continue
        seen.add(item)
        paths.append(item)
    return paths


def _safe_workspace_file(path: str, root: Path) -> Optional[str]:
    """Return a POSIX-relative path if *path* is a file inside *root*."""
    if path.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", path):
        # Absolute inputs are only accepted when they already live under root.
        candidate = Path(path)
    else:
        candidate = root / path
    try:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            return None
        if not resolved.is_file():
            return None
        rel = resolved.relative_to(root).as_posix()
    except (OSError, ValueError):
        return None
    if not rel or rel.startswith(".."):
        return None
    return rel
