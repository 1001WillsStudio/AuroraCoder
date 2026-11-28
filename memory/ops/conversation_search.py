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
  - The agent's native ``search_conversations`` tool (the first
    user-facing cross-conversation workflow) calls
    ``search_conversation_transcripts`` through the gateway during a live
    turn. That entry point deliberately scans EVERY user chat and the
    WHOLE visible transcript, so it is kept separate from the
    recency-capped, opening-only ``search_conversations`` used by the
    write-pass — same store and projection rules, different tradeoffs.

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

import re
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from gateway.attached_files import strip_attached_files
from gateway.conversation_store import store
from gateway.task_instruction_display import strip_task_instruction
from memory.ops.similarity import tokens
from src.message_provenance import is_synthetic_handoff

# Only look at the N most recently touched conversations before scoring —
# same "recency-cap before ranking" pattern as retrieval.py's RECENCY_CAP,
# so a long-lived install never makes this scan slow.
# Applies to ``search_conversations`` only: the agent-facing
# ``search_conversation_transcripts`` below deliberately scans every
# ordinary user chat and the whole transcript.
RECENCY_CAP = 30
MAX_CHARS_PER_CONVERSATION = 4000

# ── Agent-facing full-transcript search ────────────────────────────────
# The snippet window is centred on the first match in a message so the
# agent sees the passage that actually matched, not the opening of a
# possibly very long conversation.
SNIPPET_RADIUS = 140
MAX_SNIPPETS_PER_CONVERSATION = 3

_ASCII_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_CJK_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# ── Derived visible-projection cache ────────────────────────────────────
# Every full-transcript search parses each stored conversation, although only
# the visible projection is needed. That projection is derived *only* from
# text a reader may see (the user's own lines and final assistant answers), so
# caching it cannot expose anything new. A long-running gateway performs
# several lookups per turn; without this each one re-reads and re-parses the
# whole store. Entries are revalidated against the message file's on-disk
# identity before every use, so appends, edits, deletes and overwrites are
# picked up immediately instead of being served from a stale copy.
VISIBLE_CACHE_MAX_CONVERSATIONS = 2048
VISIBLE_CACHE_MAX_CHARS = 24_000_000

# key: (storage dir, conversation id) -> (file signature, projected, chars)
_visible_cache: "OrderedDict[Tuple[str, str], Tuple[tuple, List[Dict[str, Any]], int]]" = (
    OrderedDict()
)
_visible_cache_chars = 0
_visible_cache_lock = threading.Lock()

# Tiny stop-word list: short function words would otherwise match almost
# every English transcript and drown out the meaningful terms.
_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "for",
    "from", "how", "in", "is", "it", "me", "my", "of", "on", "or",
    "that", "the", "this", "to", "was", "we", "what", "when", "with",
    "you", "your",
})


def _is_tool_round(msg: Dict) -> bool:
    """True when an assistant message asked for tools instead of answering.

    The loop persists that message *before* the tools run (so the UI can show
    them), which means its ``content`` is progress narration — "let me check
    the runbook" — on the way to the real answer, not the answer itself.  When
    the round is interrupted or errors, nothing follows it, so it can end up
    as the transcript's last assistant entry.  Either way it is in-flight work.
    """
    tool_calls = msg.get("tool_calls")
    if not tool_calls:
        return False
    if isinstance(tool_calls, list):
        return any(isinstance(call, dict) for call in tool_calls)
    return True


def project_visible_messages(messages: List[Dict]) -> List[Dict[str, Any]]:
    """Reduce a stored transcript to what another chat may see.

    Only two things survive: the user's typed text (transport wrappers such
    as ``[TASK INSTRUCTION]`` / ``[ATTACHED FILES]`` stripped) and the FINAL
    assistant text of each turn. Tool calls/results, system prompts,
    reasoning/thinking and in-flight assistant drafts are private and never
    leave the store.

    A tool round has no visible text even when it carries some: an interrupted
    or errored round never produces a final answer, and replaying its progress
    prose as one would misrepresent the turn's decision.

    ``position`` is the message's index in the raw stored list. The store is
    append-only within a conversation, so positions stay stable across
    reads and are safe to use as pagination cursors.
    """
    projected: List[Dict[str, Any]] = []
    pending_assistant: Optional[Dict[str, Any]] = None

    def _flush_assistant() -> None:
        nonlocal pending_assistant
        if pending_assistant is not None:
            projected.append(pending_assistant)
            pending_assistant = None

    for position, msg in enumerate(messages or []):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "user":
            _flush_assistant()
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            text = strip_attached_files(strip_task_instruction(content)).strip()
            if text and not is_synthetic_handoff(content, msg.get("source")):
                projected.append({"position": position, "role": "user", "text": text})
        elif role == "assistant":
            if msg.get("isError"):
                continue
            if _is_tool_round(msg):
                # Mid-turn work: it supersedes any draft before it and may
                # never stand in for the turn's answer.
                pending_assistant = None
                continue
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                # Later assistant text in the same turn supersedes earlier
                # drafts, so only the turn's final answer is kept.
                pending_assistant = {
                    "position": position,
                    "role": "assistant",
                    "text": content.strip(),
                }
    _flush_assistant()
    return projected


def _query_terms(query: str) -> List[str]:
    """Terms used for multilingual matching (English words + CJK bigrams).

    Chinese is written without spaces, so whitespace tokenization would treat
    a whole sentence as one unmatchable term. Sliding two-character windows
    are the standard cheap approximation for CJK word boundaries.
    """
    text = (query or "").lower()
    terms: List[str] = []
    seen = set()

    for word in _ASCII_WORD_RE.findall(text):
        if len(word) < 2 or word in _STOP_WORDS or word in seen:
            continue
        seen.add(word)
        terms.append(word)

    for run in _CJK_RUN_RE.findall(text):
        pieces = [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
        for piece in pieces:
            if piece in seen:
                continue
            seen.add(piece)
            terms.append(piece)

    return terms


def _term_pattern(term: str) -> re.Pattern:
    """Compiled matcher: substring for CJK, word-boundary for Latin text."""
    if _CJK_CHAR_RE.search(term):
        return re.compile(re.escape(term))
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")


def clear_visible_transcript_cache() -> None:
    """Drop every cached projection (test isolation / explicit invalidation)."""
    global _visible_cache_chars
    with _visible_cache_lock:
        _visible_cache.clear()
        _visible_cache_chars = 0


def _cache_scope(active_store=None) -> Optional[str]:
    """Identity of the store a cache entry belongs to (its directory).

    Including it keeps two stores that happen to reuse a conversation id
    (for example separate test fixtures) from sharing projections.
    """
    target = store if active_store is None else active_store
    directory = getattr(target, "storage_dir", None)
    return None if directory is None else str(directory)


def _cache_forget(key: Tuple[str, str]) -> None:
    global _visible_cache_chars
    with _visible_cache_lock:
        entry = _visible_cache.pop(key, None)
        if entry is not None:
            _visible_cache_chars -= entry[2]


def _cache_put(
    key: Tuple[str, str],
    signature: tuple,
    projected: List[Dict[str, Any]],
) -> None:
    global _visible_cache_chars
    chars = sum(len(message.get("text") or "") for message in projected)
    if chars > VISIBLE_CACHE_MAX_CHARS:
        # One enormous chat must not evict everything else for little gain.
        _cache_forget(key)
        return
    with _visible_cache_lock:
        previous = _visible_cache.pop(key, None)
        if previous is not None:
            _visible_cache_chars -= previous[2]
        _visible_cache[key] = (signature, projected, chars)
        _visible_cache_chars += chars
        while _visible_cache and (
            len(_visible_cache) > VISIBLE_CACHE_MAX_CONVERSATIONS
            or _visible_cache_chars > VISIBLE_CACHE_MAX_CHARS
        ):
            _, (_, _, evicted_chars) = _visible_cache.popitem(last=False)
            _visible_cache_chars -= evicted_chars


def visible_messages(
    conversation_id: str,
    store_override: Any = None,
) -> List[Dict[str, Any]]:
    """Visible projection of one stored conversation, cached behind a stat check.

    The cached value is exactly ``project_visible_messages`` output — the same
    text a history read may return — and it is validated against the message
    file's current identity before reuse. A missing or unreadable file yields
    no messages and drops the entry, so deleted conversations are never
    replayed from cache. The returned list is shared: callers must treat it as
    read-only (the gateway's read page copies the messages it returns).

    ``store_override`` lets a caller that owns a different store binding (the
    gateway history module) read through that store instead of this module's
    default.
    """
    active_store = store if store_override is None else store_override
    directory = _cache_scope(active_store)
    signature_of = getattr(active_store, "messages_signature", None)
    if directory is None or not callable(signature_of):
        return project_visible_messages(active_store.get_messages(conversation_id))

    key = (directory, conversation_id)
    signature = signature_of(conversation_id)
    if signature is None:
        _cache_forget(key)
        return []

    with _visible_cache_lock:
        entry = _visible_cache.get(key)
        if entry is not None and entry[0] == signature:
            _visible_cache.move_to_end(key)
            return entry[1]

    snapshot = getattr(active_store, "read_messages_snapshot", None)
    if callable(snapshot):
        # Signature from the open descriptor: a concurrent atomic replace can
        # never label old content with the new file's identity.
        read_signature, raw = snapshot(conversation_id)
        if read_signature is None:
            _cache_forget(key)
            return []
        signature = read_signature
    else:
        raw = active_store.get_messages(conversation_id)

    projected = project_visible_messages(raw)
    _cache_put(key, signature, projected)
    return projected


def _snippet_around(text: str, start: int, end: int) -> str:
    """Window of *text* around a match, with ellipses when clipped."""
    lo = max(0, start - SNIPPET_RADIUS)
    hi = min(len(text), end + SNIPPET_RADIUS)
    excerpt = text[lo:hi].strip()
    if lo > 0:
        excerpt = "…" + excerpt
    if hi < len(text):
        excerpt = excerpt + "…"
    return excerpt


def search_conversation_transcripts(
    query: str,
    exclude_conversation_id: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Search every ordinary user chat's visible transcript (agent tool).

    Unlike ``search_conversations`` (recency-capped, 4K chars of the opening,
    token-set overlap, used by the memory write-pass), this scans ALL stored
    user chats and the WHOLE visible transcript, matches Chinese and English
    terms as substrings, and returns snippets taken next to the match.

    Each result: ``{"conversation_id", "title", "created_at",
    "updated_at", "score", "match_count", "snippets": [{"position",
    "role", "text"}]}``. Subagent/worker chats are never returned.
    """
    # Cap the query: a pathologically long one would mint a huge number of
    # CJK bigrams/patterns for no benefit.
    terms = _query_terms(query[:500])
    if not terms:
        return []

    patterns = [(term, _term_pattern(term)) for term in terms]
    total_weight = sum(len(term) for term, _ in patterns) or 1

    results: List[Dict[str, Any]] = []
    for meta in store.list_conversations(conv_type="user_chat"):
        cid = meta.get("id")
        if not cid or cid == exclude_conversation_id or meta.get("parent_id"):
            continue

        matched = set()
        snippets: List[Dict[str, Any]] = []
        match_count = 0

        for message in visible_messages(cid):
            lowered = message["text"].lower()
            first_hit = None
            for term, pattern in patterns:
                hit = pattern.search(lowered)
                if hit is None:
                    continue
                matched.add(term)
                match_count += len(pattern.findall(lowered))
                if first_hit is None or hit.start() < first_hit[0]:
                    first_hit = (hit.start(), hit.end())
            if first_hit is not None and len(snippets) < MAX_SNIPPETS_PER_CONVERSATION:
                snippets.append({
                    "position": message["position"],
                    "role": message["role"],
                    "text": _snippet_around(message["text"], *first_hit),
                })

        if not matched:
            continue

        score = round(sum(len(term) for term in matched) / total_weight, 4)
        results.append({
            "conversation_id": cid,
            "title": meta.get("title", "") or "Untitled",
            "created_at": meta.get("created_at", ""),
            "updated_at": meta.get("updated_at", ""),
            "score": score,
            "match_count": match_count,
            "snippets": snippets,
        })

    results.sort(
        key=lambda r: (r["score"], r["match_count"], r.get("updated_at", "")),
        reverse=True,
    )
    return results[: max(1, int(limit))]


def _conversation_text(conversation_id: str) -> str:
    """Compact text used for scoring: user/assistant message content only
    (tool calls/results are noise for topical search, same reasoning as
    ``extractor._transcript_to_text``)."""
    parts: List[str] = []
    for msg in store.get_messages(conversation_id):
        if msg.get("role") in ("user", "assistant"):
            content = (msg.get("content") or "").strip()
            if msg["role"] == "user" and is_synthetic_handoff(content, msg.get("source")):
                continue
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
    ``{"conversation_id", "title", "snippet", "score", "created_at",
    "updated_at"}`` — the timestamps let the write-pass judge WHEN
    corroboration happened (recent repeats vs. a single mention long ago),
    which a ``created_at`` alone can't. Empty query,
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
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", ""),
            }
            for score, meta, text in scored[:limit]
        ]
    except Exception:
        return []
