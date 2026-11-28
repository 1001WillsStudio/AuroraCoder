"""Bound terminal log reads before decoding supervisor output."""

import os
from pathlib import Path


def truncate_terminal_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head_size = (limit + 1) // 2
    tail_size = limit // 2
    head = text[:head_size]
    tail = text[-tail_size:] if tail_size else ""
    dropped = len(text) - limit
    return f"{head}\n\n... [{dropped:,} characters truncated] ...\n\n{tail}"


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


MAX_SAVED_OUTPUT_BYTES = 2_000_000


def read_saved_terminal_output(path: Path, preview_limit: int) -> str | None:
    """Capture bounded, completed stdout while the shell still owns its log file."""
    with path.open("rb") as source:
        size = os.fstat(source.fileno()).st_size
        if size <= preview_limit or size > MAX_SAVED_OUTPUT_BYTES:
            return None
        text = _decode(source.read(MAX_SAVED_OUTPUT_BYTES + 1))
    return text if len(text) > preview_limit else None


def read_terminal_output(path: Path, limit: int) -> str:
    """Return a first/last excerpt, reading at most four bytes per display character."""
    with path.open("rb") as source:
        size = os.fstat(source.fileno()).st_size
        if size <= limit * 4:
            return truncate_terminal_text(_decode(source.read(limit * 4)), limit)

        head_size = (limit + 1) // 2
        tail_size = limit // 2
        head = _decode(source.read(head_size * 4))[:head_size]
        tail = ""
        if tail_size:
            source.seek(-tail_size * 4, os.SEEK_END)
            suffix = source.read(tail_size * 4)
            start = 0
            while start < len(suffix) and suffix[start] & 0xC0 == 0x80:
                start += 1
            tail = _decode(suffix[start:])[-tail_size:]
    return f"{head}\n\n... [{size:,} bytes of output; middle truncated] ...\n\n{tail}"
