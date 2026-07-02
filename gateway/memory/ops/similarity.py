"""
Shared keyword-overlap similarity helper — no embeddings, consistent with
this MVP's retrieval approach elsewhere (see retrieval.py, gap_store.py).

Used by both ``consolidator.py`` (post-write dedupe/decay across the whole
store) and ``extractor.py`` (pre-write duplicate lookup, so the model can
merge into an existing memory instead of creating a near-duplicate one).
Factored out so both call sites use the exact same notion of "similar" —
previously each had its own near-identical copy of this logic.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

_WORD_RE = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> set:
    return set(w for w in _WORD_RE.findall(text.lower()) if len(w) > 2)


def similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def find_similar_existing(repo, plane: str, scope: str, description: str, limit: int = 5, threshold: float = 0.15) -> List[Dict[str, Any]]:
    """Cheap keyword-overlap search over existing memories in the same
    plane/scope — enough context for a model to catch "this refines an
    existing memory" without needing embeddings.
    """
    cand_tokens = tokens(description)
    if not cand_tokens:
        return []

    scored = []
    for row in repo.list(plane=plane, scope=scope):
        other_tokens = tokens(row["description"])
        if not other_tokens:
            continue
        overlap = len(cand_tokens & other_tokens) / len(cand_tokens | other_tokens)
        if overlap > threshold:
            scored.append((overlap, row))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [
        {"id": row["id"], "description": row["description"], "type": row["type"], "confidence": row["confidence"]}
        for _, row in scored[:limit]
    ]
