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
from typing import Any, Iterable, List, Optional, Sequence

from gateway.paths import WorkspacePathError, resolve_under_workspace

ATTACHED_FILES_START = "[ATTACHED FILES]"
ATTACHED_FILES_END = "[/ATTACHED FILES]"
ATTACHED_FILES_INSTRUCTION = (
    "Read each of the following workspace files with read_file before acting:"
)
MAX_ATTACHED_FILES = 8


class AttachedFilesError(ValueError):
    """Invalid ``attached_files`` payload, or no usable workspace files."""


_ATTACHED_BLOCK_RE = re.compile(
    re.escape(ATTACHED_FILES_START)
    + r"(.*?)"
    + re.escape(ATTACHED_FILES_END)
    + r"\s*",
    re.DOTALL,
)


def coerce_attached_files(value: Any) -> Optional[List[str]]:
    """Return a path list, ``None`` if omitted, or raise ``AttachedFilesError``.

    A bare string must not be iterated (that would treat each character as
    a path). Non-string items are also refused so the route can 400.
    """
    if value is None:
        return None
    if not isinstance(value, list):
        raise AttachedFilesError("attached_files must be a list of paths")
    out: List[str] = []
    for item in value:
        if not isinstance(item, str):
            raise AttachedFilesError("attached_files must be a list of paths")
        out.append(item)
    return out


def apply_request_attachments(body: dict, workspace: Optional[Path]) -> List[str]:
    """Validate ``attached_files`` on a chat body and rewrite ``message``.

    Pops ``attached_files`` so it is not forwarded to the agent backend.
    Raises ``AttachedFilesError`` when the field is the wrong type, or when
    attachments were requested but none survived validation — the caller
    should 400 rather than start a files-only turn with ``message=None``.
    """
    requested = coerce_attached_files(body.pop("attached_files", None))
    message = body.get("message")
    has_marker = isinstance(message, str) and ATTACHED_FILES_START in message
    if not requested and not has_marker:
        return []
    text = message if isinstance(message, str) else ""
    wrapped, kept = prepare_attached_message(text, requested, workspace)
    wanted = list(requested or [])
    if not wanted and has_marker:
        wanted = extract_attached_files(text) or []
    if wanted and not kept:
        raise AttachedFilesError(
            "None of the attached workspace files could be used. "
            "They must be existing files inside the workspace."
        )
    body["message"] = wrapped if wrapped else None
    return kept


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
    try:
        resolved = resolve_under_workspace(root, path)
    except WorkspacePathError:
        return None
    if not resolved.is_file():
        return None
    try:
        rel = resolved.relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return None
    return rel or None
