"""
World-Model retrieval — recency-capped candidates, ranked by keyword
overlap + usage/recency (design doc §12).

Deliberately no embeddings dependency for v1: a BM25-ish keyword score
over ``description`` (the field every memory is indexed by, per Claude
Code / Cursor precedent) is enough to start, and keeps this module
dependency-free. Swap in embeddings later behind the same
``rank_candidates`` signature if keyword recall proves too weak.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Dict, List

from .schema import MemoryItem
from .store import MemoryRepository

_WORD_RE = re.compile(r"[a-z0-9_]+")

# Bound the candidate pool before ranking (§12 step 1: "recency-cap
# candidates") so a large store never makes retrieval slow.
RECENCY_CAP = 200


def _tokenize(text: str) -> List[str]:
    return _WORD_RE.findall(text.lower())


def _keyword_score(query_tokens: List[str], item: MemoryItem) -> float:
    if not query_tokens:
        return 0.0
    haystack = _tokenize(f"{item.description} {item.content}")
    if not haystack:
        return 0.0
    haystack_set = set(haystack)
    hits = sum(1 for t in query_tokens if t in haystack_set)
    return hits / len(query_tokens)


def _recency_score(item: MemoryItem) -> float:
    """0..1, decaying over ~30 days since last use (or creation)."""
    ts = item.last_used or item.created
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    return math.exp(-age_days / 30.0)


def _usage_score(item: MemoryItem) -> float:
    return math.log1p(item.usage_count)


def rank_candidates(
    repo: MemoryRepository,
    query: str,
    plane: str = "world",
    scope: str | None = None,
    k: int = 5,
) -> List[Dict]:
    """Return the top-*k* memories for *query*, each with its component scores.

    Ranking = 0.6 * keyword_overlap + 0.25 * recency + 0.15 * usage — a
    fixed blend for v1 (design doc §17 flags hand-set weights as a thing
    to eventually replace with relative/learned salience; this is the
    honest cold-start prior called out there).
    """
    candidates = repo.all_items(plane=plane, scope=scope)
    # Recency-cap: only consider the most recently touched/created N items.
    candidates.sort(key=lambda it: it.last_used or it.created, reverse=True)
    candidates = candidates[:RECENCY_CAP]

    query_tokens = _tokenize(query)
    scored = []
    for item in candidates:
        kw = _keyword_score(query_tokens, item)
        rec = _recency_score(item)
        use = _usage_score(item)
        total = 0.6 * kw + 0.25 * rec + 0.15 * use
        scored.append((total, item, {"keyword": kw, "recency": rec, "usage": use}))

    scored.sort(key=lambda t: t[0], reverse=True)
    top = scored[:k]

    return [
        {
            **item.to_dict(),
            "score": round(total, 4),
            "score_components": components,
        }
        for total, item, components in top
        if total > 0 or not query_tokens  # empty query → just return most recent/used
    ]
