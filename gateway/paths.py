"""Shared workspace path containment.

HTTP file endpoints and chat attachments both need to know whether a
user-supplied path lives inside the workspace.  ``str.startswith`` on the
resolved strings is not enough: a sibling named ``ws-leaked`` next to
workspace ``ws`` still prefixes.  ``Path.is_relative_to`` is the check.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

PathLike = Union[str, Path]


class WorkspacePathError(ValueError):
    """Invalid path, or a path that does not live inside the workspace."""

    def __init__(self, message: str, *, outside: bool = False):
        super().__init__(message)
        self.outside = outside


def resolve_under_workspace(work_dir: PathLike, path: str) -> Path:
    """Resolve *path* and require it to live inside *work_dir*.

    Relative inputs join *work_dir*. Absolute inputs are accepted only
    when they already resolve under it. The returned path is
    ``Path.resolve()``'d (symlinks followed), so a workspace symlink
    that points outside is rejected.

    Raises:
        WorkspacePathError: *outside=True* when the path escapes;
            otherwise the input could not be resolved.
    """
    if not isinstance(path, str):
        raise WorkspacePathError("Invalid path")
    try:
        root = Path(work_dir).resolve()
    except (OSError, TypeError, ValueError) as exc:
        raise WorkspacePathError("Invalid workspace") from exc

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / path
    try:
        resolved = candidate.resolve()
    except (OSError, ValueError) as exc:
        raise WorkspacePathError("Invalid path") from exc

    if not resolved.is_relative_to(root):
        raise WorkspacePathError("Path is outside the workspace", outside=True)
    return resolved
