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

## Why decay does NOT trust self-reported ``confidence`` for exemption

An earlier version gave anything with ``confidence == "high"`` permanent
immunity from decay. That's backwards: the write-pass LLM self-rates its
own confidence with no external anchor, which is exactly the setup where
models skew toward rating everything "high" — and if it does, this
mechanism goes silently toothless (nothing ever gets cleaned up, and
wrong facts marked "high" would live forever with false authority).

The fix applied here is an asymmetric-trust rule: self-reported
confidence is only ever allowed to make a memory decay *faster*
(``low`` confidence — a safe, recoverable direction to trust a
self-report in), never to grant *longer life* on its own. Longer life is
earned only through evidence the model can't fabricate by simply
choosing a word:
  - ``corroboration_count`` — how many separate, later sessions
    independently re-affirmed this exact memory (see
    ``ops/extractor.py``'s handling of ``duplicate_of`` — this is
    incremented in code, never self-reported).
  - actual retrieval usage (``usage_count`` / ``last_used``) — but even
    that isn't permanent immunity anymore; see ``STALE_USED_MULTIPLIER``
    below, which closes the older "retrieved once, immortal forever"
    gap in the original logic.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from memory.schema import MemoryItem
from memory.store import MemoryRepository, get_repository
from memory.ops.similarity import similarity as _similarity

logger = logging.getLogger(__name__)

# Starting priors (see module docstring) — not tuned, intentionally conservative.
DECAY_MAX_UNUSED_DAYS = 90
DEDUPE_SIMILARITY_THRESHOLD = 0.82

# Self-reported "low" confidence is trusted to decay a memory faster (safe
# direction — worst case we lose something that was fine). Self-reported
# "medium"/"high" get no multiplier at all; only corroboration_count (below)
# can extend life, since that's evidence, not a self-rating.
DECAY_LOW_CONFIDENCE_MULTIPLIER = 0.5

# Each independent re-affirmation extends the grace period, capped so a
# memory can earn a long life through real evidence without becoming
# literally permanent from self-report alone.
DECAY_CORROBORATION_CAP = 6

# A memory that HAS been retrieved-and-used before still isn't immortal —
# it just gets a longer allowance (relative to its own grace period) measured
# from its last hit, not its creation date, before it's considered stale again.
STALE_USED_MULTIPLIER = 3


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


def _effective_grace_days(item: MemoryItem, base_days: int) -> float:
    """How long an unused world memory gets before it's eligible for decay.

    See module docstring for why this is built from confidence (only ever
    shortens it) and corroboration_count (only source of extra life),
    never from confidence alone.
    """
    confidence_mult = DECAY_LOW_CONFIDENCE_MULTIPLIER if item.confidence == "low" else 1.0
    corroboration_mult = min(1 + item.corroboration_count, DECAY_CORROBORATION_CAP)
    return base_days * confidence_mult * corroboration_mult


def decay_unused_world_memories(repo: MemoryRepository, max_unused_days: int = DECAY_MAX_UNUSED_DAYS) -> int:
    """Drop world-plane memories that have earned no evidence-based reason
    to keep living. Never touches the stance plane (see module docstring).

    Three independent checks, any of which removes the item:
    1. Volatile + past its ``ttl_days`` — time-bound by design (design doc
       §10: "carries ttl, re-verify on read"); since there's no
       re-verify-on-read mechanism yet, honest behavior is to expire on
       schedule rather than let it linger unverified.
    2. Never retrieved, and older than its confidence/corroboration-scaled
       grace period (see ``_effective_grace_days``).
    3. Retrieved before, but not recently enough — closes the old "used
       once, immortal forever" gap. Measured from ``last_used`` with a
       longer (``STALE_USED_MULTIPLIER``x) allowance, since a track record
       of being useful is real evidence, just not permanent proof.
    """
    items = repo.all_items(plane="world")
    removed = 0
    for item in items:
        if item.volatile and item.ttl_days is not None and _age_days(item.created) >= item.ttl_days:
            repo.delete(item.id)
            removed += 1
            logger.info("[memory-consolidate] Expired volatile memory %s (%s, ttl=%dd)",
                        item.id, item.description, item.ttl_days)
            continue

        grace = _effective_grace_days(item, max_unused_days)
        if item.usage_count == 0:
            if _age_days(item.created) < grace:
                continue
        else:
            reference = item.last_used or item.created
            if _age_days(reference) < grace * STALE_USED_MULTIPLIER:
                continue

        repo.delete(item.id)
        removed += 1
        logger.info(
            "[memory-consolidate] Decayed memory %s (%s, confidence=%s, corroboration=%d, usage=%d, grace=%.0fd)",
            item.id, item.description, item.confidence, item.corroboration_count, item.usage_count, grace,
        )
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
