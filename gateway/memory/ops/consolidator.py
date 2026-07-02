"""
Consolidation + decay (Layer 2a) — design doc §11 "Consolidate + decay":
dedupe/merge (update-before-create), drop long-unused, keep frequently
cited.

Deliberately heuristic (no LLM call) for v1: a naive description-overlap
dedupe plus a conservative unused-decay pass. This is the "estimated
from real usage, not a magic number I invented" honesty the design doc
asks for in §17 — the thresholds here are round numbers chosen as a
starting prior, not tuned; §17's adaptive-salience add-on is the
documented upgrade path once there's real usage data to learn from.

Never touches the ``stance`` plane automatically — explicit
preferences/corrections are exactly what §15 calls "worth storing once"
and should only ever be removed by an explicit ``remember`` overwrite or
a human editing the file directly.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from gateway.memory.schema import MemoryItem
from gateway.memory.store import MemoryRepository, get_repository

logger = logging.getLogger(__name__)

# Starting priors (see module docstring) — not tuned, intentionally conservative.
DECAY_MAX_UNUSED_DAYS = 90
DEDUPE_SIMILARITY_THRESHOLD = 0.82


_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set:
    return set(w for w in _WORD_RE.findall(text.lower()) if len(w) > 2)


def _similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _age_days(iso_ts: str) -> float:
    try:
        dt = datetime.fromisoformat(iso_ts)
    except (ValueError, TypeError):
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def dedupe_world_memories(repo: MemoryRepository) -> int:
    """Merge near-duplicate world-plane memories (same type+scope, similar
    description). Keeps the item with higher usage_count (ties broken by
    recency), deletes the rest. Returns number of items removed."""
    items = repo.all_items(plane="world")
    groups: Dict[Tuple[str, str], List[MemoryItem]] = {}
    for item in items:
        groups.setdefault((item.type, item.scope), []).append(item)

    removed = 0
    for _, group in groups.items():
        if len(group) < 2:
            continue
        # Greedy clustering by description similarity within the group.
        used = set()
        for i, a in enumerate(group):
            if a.id in used:
                continue
            cluster = [a]
            for b in group[i + 1:]:
                if b.id in used:
                    continue
                if _similarity(a.description, b.description) >= DEDUPE_SIMILARITY_THRESHOLD:
                    cluster.append(b)
                    used.add(b.id)
            if len(cluster) > 1:
                cluster.sort(key=lambda it: (it.usage_count, it.last_used or it.created), reverse=True)
                keeper, dupes = cluster[0], cluster[1:]
                for dupe in dupes:
                    repo.delete(dupe.id)
                    removed += 1
                    logger.info("[memory-consolidate] Merged %s into %s (duplicate description)", dupe.id, keeper.id)
            used.add(a.id)

    return removed


def decay_unused_world_memories(repo: MemoryRepository, max_unused_days: int = DECAY_MAX_UNUSED_DAYS) -> int:
    """Drop world-plane, non-volatile-critical memories that have never
    been retrieved and are older than *max_unused_days*. Never touches
    the stance plane (see module docstring) or anything with
    confidence='high' (explicit, high-trust facts survive longer)."""
    items = repo.all_items(plane="world")
    removed = 0
    for item in items:
        if item.usage_count > 0:
            continue
        if item.confidence == "high":
            continue
        if _age_days(item.created) < max_unused_days:
            continue
        repo.delete(item.id)
        removed += 1
        logger.info("[memory-consolidate] Decayed unused memory %s (%s, age>%dd)", item.id, item.description, max_unused_days)
    return removed


def run_consolidation() -> Dict[str, int]:
    """Run the full consolidation pass. Never raises — a failed
    housekeeping pass should never surface as a user-visible error."""
    try:
        repo = get_repository()
        merged = dedupe_world_memories(repo)
        decayed = decay_unused_world_memories(repo)
        return {"merged": merged, "decayed": decayed}
    except Exception:
        logger.exception("[memory-consolidate] Consolidation pass failed")
        return {"merged": 0, "decayed": 0}
