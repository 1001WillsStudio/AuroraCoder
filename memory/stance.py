"""
Stance Provider — assembles the always-injected "attitude" block
(design doc §7.2.A, §12).

Stance is small by design: capped to the top-N items by usage/recency so
it stays cheap enough to sit in the cached system-prompt prefix on every
turn (never busts prompt caching, unlike World-Model retrieval which is
injected out of the prefix — see §20 "Caching").
"""

from __future__ import annotations

from typing import List

from .schema import MemoryItem
from .store import MemoryRepository

MAX_STANCE_ITEMS = 15

_TYPE_LABEL = {
    "preference": "Preference",
    "feedback": "Correction",
    "communication": "Communication style",
    "autonomy": "Autonomy",
}


def _sort_key(item: MemoryItem):
    # Usage first (proven useful), then recency — matches §12's
    # "top-15 by usage/recency" for the Stance cap.
    return (item.usage_count, item.last_used or item.created)


def build_stance_block(repo: MemoryRepository, scope: str | None = None) -> str:
    """Return the Stance text to inject into the system prompt, or "" if empty.

    Empty Stance is the common case for a new install — the block is
    entirely omitted rather than injecting an empty header, keeping the
    "no-op is the default" discipline (§15) even at the injection layer.
    """
    items = repo.all_items(plane="stance", scope=scope)
    if not items:
        return ""

    items.sort(key=_sort_key, reverse=True)
    top = items[:MAX_STANCE_ITEMS]

    lines: List[str] = []
    for item in top:
        label = _TYPE_LABEL.get(item.type, item.type.capitalize())
        lines.append(f"- [{label}] {item.content.strip()}")

    repo.bump_usage([it.id for it in top])

    return (
        "**What you know about this user/project (from memory):**\n"
        + "\n".join(lines)
    )
