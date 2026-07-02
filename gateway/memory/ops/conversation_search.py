"""
Cross-conversation search — deterministic, keyword-overlap, no LLM, no
agent loop.

Built ONCE here so multiple consumers share a single implementation of
"what does this user's other sessions say about X" instead of each
independently reaching into all stored conversation history:

  - The memory write-pass (``ops/extractor.py``) calls this directly,
    in-process, as a plain function — same pattern as the existing
    per-nomination duplicate lookup against the memory store
    (``ops/similarity.find_similar_existing``). It stays deterministic
    pre-fetched context for a single LLM call, not something the model
    decides to invoke — extraction does not become an agent just because
    it now sees a bit more context.
  - Layer 2b's gap-investigation worker (when its investigate/report
    protocol is eventually built) would call the *same* logic — but
    since that worker runs in its own isolated container, it reaches it
    over HTTP via a gateway route instead of importing this module
    directly. One search implementation, two access paths — not two
    separate implementations of "search everything".

Scope: AuroraCoder runs exactly one workspace per running gateway
instance (see ``src/config.py`` ``WORKSPACE_DIR`` — a single global
path, not per-conversation), so every conversation this gateway has ever
stored already belongs to the same project by construction. There is no
separate "same project" filter to apply here — restricting to
conversations already recorded in THIS gateway's store already *is*
project scoping. If AuroraCoder ever supports multiple concurrent
workspaces from one gateway, this is the one place that would need a
project/workspace filter added.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gateway.conversation_store import store
from gateway.memory.ops.similarity import tokens

# Only look at the N most recently touched conversations before scoring —
# same "recency-cap before ranking" pattern as retrieval.py's RECENCY_CAP,
# so a long-lived install never makes this scan slow.
RECENCY_CAP = 30
MAX_CHARS_PER_CONVERSATION = 4000


def _conversation_text(conversation_id: str) -> str:
    """Compact text used for scoring: user/assistant message content only
    (tool calls/results are noise for topical search, same reasoning as
    ``extractor._transcript_to_text``)."""
    parts: List[str] = []
    for msg in store.get_messages(conversation_id):
        if msg.get("role") in ("user", "assistant"):
            content = (msg.get("content") or "").strip()
            if content:
                parts.append(content)
    return " ".join(parts)[:MAX_CHARS_PER_CONVERSATION]


def search_conversations(
    query: str,
    exclude_conversation_id: Optional[str] = None,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Keyword-overlap search over other top-level user-chat conversations.

    Returns up to *limit* results, most relevant first, each
    ``{"conversation_id", "title", "snippet", "score"}``. Empty query,
    empty store, or no overlap at all -> ``[]`` (never raises — callers
    treat this as best-effort context, not a required dependency).
    """
    try:
        query_tokens = tokens(query)
        if not query_tokens:
            return []

        convs = [
            c for c in store.list_conversations(conv_type="user_chat")
            if c.get("id") and c.get("id") != exclude_conversation_id
        ]
        convs.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        convs = convs[:RECENCY_CAP]

        scored = []
        for meta in convs:
            text = f"{meta.get('title', '')} {_conversation_text(meta['id'])}"
            text_tokens = tokens(text)
            if not text_tokens:
                continue
            overlap = len(query_tokens & text_tokens) / len(query_tokens | text_tokens)
            if overlap > 0:
                scored.append((overlap, meta, text))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            {
                "conversation_id": meta["id"],
                "title": meta.get("title", ""),
                "snippet": text[:300],
                "score": round(score, 4),
            }
            for score, meta, text in scored[:limit]
        ]
    except Exception:
        return []
