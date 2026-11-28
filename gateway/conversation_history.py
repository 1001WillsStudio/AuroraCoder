"""Read-only cross-conversation access for the agent's native tools.

The gateway owns conversation storage; the backend agent loop must not read
that store directly (see AGENT_README.md — ``src/`` is stateless).  So the
three native tools (``list_conversations``, ``search_conversations``,
``read_conversation``) call the gateway over HTTP, exactly like
``memory_client`` does for memory.

Authorization model
-------------------
A chat turn is not allowed to replay the user's login token to the provider or
to the backend tools, and there is no general "internal API" bypass.  Instead
the gateway proxy issues a **per-turn, per-conversation, read-only
credential** (``TURN_TOKEN_HEADER``) when it admits a turn, and forwards it
only to the backend for that one turn.  The credential:

  * is random and unguessable (``secrets.token_urlsafe``);
  * is bound to the conversation that asked for it, which is also the
    conversation excluded from every result;
  * carries no user identity and grants nothing except these three reads;
  * lives exactly as long as the turn that owns it: the proxy revokes it on
    completion, cancel or error, and an active turn never ages out mid-flight.

History returned by these tools is *data*, never new authority: seeing text
inside an old transcript can never widen this credential's scope.

The desktop conversation drawer is the one *user-facing* consumer of this
module (:func:`search_conversation_history_for_browser`).  It is authenticated
by the ordinary login middleware — no per-turn credential — and it deliberately
includes the conversation on screen, because it searches history the user can
already open themselves.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from gateway.conversation_store import store
from memory.ops.conversation_search import (
    search_conversation_transcripts,
    visible_messages,
)

# Header carrying the per-turn credential.  Deliberately NOT ``Authorization``
# so a user login token can never be mistaken for one (or vice versa).
TURN_TOKEN_HEADER = "X-Aurora-Turn-Token"

# Internal route prefix.  ``gateway.api`` lets these paths past the *user
# login* middleware; each route then verifies the per-turn credential itself.
TURN_TOKEN_PREFIX = "/api/conversation-history/"
# Exact paths exempt from the user-login middleware. Exact, not a prefix:
# an unknown path under this prefix must never become an unauthenticated
# surface. Each route still verifies the per-turn credential itself.
TURN_TOKEN_PATHS = tuple(
    f"{TURN_TOKEN_PREFIX}{name}" for name in ("list", "search", "read")
)

# Lifetime of a *standalone* credential — one minted outside the proxy loop
# (tests, fixtures) and therefore not owned by a running turn.  Turn-owned
# credentials have no deadline at all: they end when their turn does, so a
# multi-hour task is never cut off mid-flight.
TURN_TOKEN_TTL_SECONDS = 2 * 60 * 60

DEFAULT_LIST_LIMIT = 10
MAX_LIST_LIMIT = 50
DEFAULT_TRANSCRIPT_LIMIT = 20
MAX_TRANSCRIPT_LIMIT = 50
DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 20

# Conversation ids are generated as UUIDs by the gateway.  Validating the
# shape keeps model-supplied ids from ever reaching a file path.
_CONVERSATION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


@dataclass
class TurnCredential:
    """What a per-turn conversation-history credential is allowed to do."""

    conversation_id: str
    # ``math.inf`` for a turn-owned credential: its turn decides, not a clock.
    expires_at: float


_credentials: Dict[str, TurnCredential] = {}
# Tokens owned by a turn the proxy is still running.  Membership — not
# ``expires_at`` — keeps a live turn authorized, so authorization is bound to
# the turn's real lifetime instead of a wall-clock age.
_live_turns: set = set()
_credentials_lock = threading.Lock()


def _now() -> float:
    """Clock indirection so tests can travel in time without patching ``time``."""
    return time.time()


def _prune_locked(now: float) -> None:
    """Drop expired standalone credentials. Caller must hold the lock.

    Turn-owned credentials are never pruned by age: their turn revokes them,
    so a long-running task keeps its credential for as long as it runs.
    """
    expired = [
        token for token, cred in _credentials.items()
        if token not in _live_turns and cred.expires_at <= now
    ]
    for token in expired:
        _credentials.pop(token, None)


def _mint(conversation_id: str, ttl: Optional[float]) -> str:
    """Mint a credential.  ``ttl=None`` ties its life to a live turn instead.

    A turn-owned credential carries no wall-clock deadline; the owning stream
    revokes it on completion, cancel or error.  ``ttl`` therefore only applies
    to standalone credentials, whose age is their only bound.
    """
    token = secrets.token_urlsafe(32)
    now = _now()
    with _credentials_lock:
        _prune_locked(now)
        expires_at = float("inf") if ttl is None else now + ttl
        _credentials[token] = TurnCredential(conversation_id, expires_at)
        if ttl is None:
            _live_turns.add(token)
    return token


def issue_turn_credential(
    conversation_id: str,
    ttl: float = TURN_TOKEN_TTL_SECONDS,
) -> str:
    """Mint a standalone credential with a fixed TTL and no owning turn.

    Used by tests and fixtures.  The proxy mints turn-owned credentials with
    :func:`begin_turn_credential` instead.
    """
    return _mint(conversation_id, ttl)


def begin_turn_credential(conversation_id: str) -> str:
    """Mint a credential whose life is the turn's, not a wall-clock age.

    The owning stream must call :func:`revoke_turn_credential` when the turn
    completes, is cancelled, or fails.
    """
    return _mint(conversation_id, None)


def revoke_turn_credential(token: Optional[str]) -> None:
    """End a credential now (turn finished, cancelled or errored)."""
    if not token:
        return
    with _credentials_lock:
        _credentials.pop(token, None)
        _live_turns.discard(token)


def resolve_turn_credential(token: Optional[str]) -> Optional[TurnCredential]:
    """Return the live credential for *token*, or ``None``."""
    if not token:
        return None
    now = _now()
    with _credentials_lock:
        cred = _credentials.get(token)
        if cred is None:
            return None
        if token in _live_turns:
            # An active turn's credential does not age out.
            return cred
        if cred.expires_at <= now:
            _credentials.pop(token, None)
            return None
        return cred


def clear_turn_credentials() -> None:
    """Drop every credential (test isolation helper)."""
    with _credentials_lock:
        _credentials.clear()
        _live_turns.clear()


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

def _is_ordinary_user_chat(meta: Optional[Dict[str, Any]]) -> bool:
    """True for top-level chats the user started.

    Subagent / worker transcripts (``type != "user_chat"``) and any child with
    a ``parent_id`` are private agent working state, not user history.
    """
    if not isinstance(meta, dict):
        return False
    return meta.get("type") == "user_chat" and not meta.get("parent_id")


def _ordinary_user_chats() -> List[Dict[str, Any]]:
    return [
        meta for meta in store.list_conversations(conv_type="user_chat")
        if _is_ordinary_user_chat(meta)
    ]


def _find_ordinary_chat(conversation_id: str) -> Optional[Dict[str, Any]]:
    if not isinstance(conversation_id, str) or not _CONVERSATION_ID_RE.fullmatch(conversation_id):
        return None
    for meta in _ordinary_user_chats():
        if meta.get("id") == conversation_id:
            return meta
    return None


def _clamp(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(maximum, parsed))


# ---------------------------------------------------------------------------
# Cursor helpers (metadata-only pagination)
# ---------------------------------------------------------------------------

def _sort_key(meta: Dict[str, Any]) -> Tuple[str, str]:
    return (meta.get("updated_at") or "", meta.get("id") or "")


def encode_cursor(meta: Dict[str, Any]) -> str:
    """Opaque cursor pointing *after* ``meta`` in the list ordering."""
    raw = json.dumps(
        {"u": meta.get("updated_at") or "", "i": meta.get("id") or ""},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> Tuple[str, str]:
    """Inverse of :func:`encode_cursor`. Raises ``ValueError`` if malformed."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        updated_at, conversation_id = payload["u"], payload["i"]
    except Exception as exc:  # binascii/json/KeyError/Unicode — all "bad cursor"
        raise ValueError("invalid cursor") from exc
    if not isinstance(updated_at, str) or not isinstance(conversation_id, str):
        raise ValueError("invalid cursor")
    return updated_at, conversation_id


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _metadata_entry(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Metadata only — never message text."""
    return {
        "conversation_id": meta.get("id"),
        "title": meta.get("title") or "Untitled",
        "created_at": meta.get("created_at") or "",
        "updated_at": meta.get("updated_at") or "",
    }


def list_conversation_metadata(
    exclude_conversation_id: Optional[str],
    limit: int = DEFAULT_LIST_LIMIT,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    """Page through ordinary user chats, newest first, metadata only.

    The cursor is the (updated_at, id) of the last item returned, so a page is
    stable even while other conversations are being written. Raises
    ``ValueError`` for a malformed cursor.
    """
    limit = _clamp(limit, DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT)
    items = [
        meta for meta in _ordinary_user_chats()
        if meta.get("id") != exclude_conversation_id
    ]
    items.sort(key=_sort_key, reverse=True)

    if cursor:
        boundary = decode_cursor(cursor)
        items = [meta for meta in items if _sort_key(meta) < boundary]

    page = items[:limit]
    next_cursor = encode_cursor(page[-1]) if len(items) > limit and page else None
    return {
        "conversations": [_metadata_entry(meta) for meta in page],
        "next_cursor": next_cursor,
    }


def read_conversation_transcript(
    conversation_id: str,
    exclude_conversation_id: Optional[str] = None,
    after: int = -1,
    limit: int = DEFAULT_TRANSCRIPT_LIMIT,
) -> Optional[Dict[str, Any]]:
    """One page of a conversation's visible transcript.

    Visible means the user's own text plus the final assistant answer of each
    turn (see :func:`project_visible_messages`).  A turn interrupted or errored
    mid-tool-round contributes no assistant text: the committed tool-call
    message holds progress narration, not the turn's decision.

    Only ordinary user chats are readable; the caller's own conversation is
    excluded. ``after`` is the last ``position`` already shown (omit / -1 to
    start at the beginning), so pagination is stable across appends. Returns
    ``None`` when the id is unknown or out of scope.
    """
    if exclude_conversation_id and conversation_id == exclude_conversation_id:
        return None
    meta = _find_ordinary_chat(conversation_id)
    if meta is None:
        return None

    limit = _clamp(limit, DEFAULT_TRANSCRIPT_LIMIT, MAX_TRANSCRIPT_LIMIT)
    try:
        after = max(-1, int(after))
    except (TypeError, ValueError):
        after = -1

    # Cached projection, revalidated against the message file on every read.
    # Copy the page so callers cannot mutate the shared cache entries.
    projected = visible_messages(conversation_id, store_override=store)
    remaining = [msg for msg in projected if msg["position"] > after]
    page = [dict(msg) for msg in remaining[:limit]]
    has_more = len(remaining) > limit

    return {
        "conversation_id": conversation_id,
        "title": meta.get("title") or "Untitled",
        "created_at": meta.get("created_at") or "",
        "updated_at": meta.get("updated_at") or "",
        "messages": page,
        "next_cursor": page[-1]["position"] if has_more and page else None,
        "total_messages": len(projected),
    }


def search_conversation_history(
    query: str,
    exclude_conversation_id: Optional[str],
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> Dict[str, Any]:
    """Full-transcript, multilingual search over ordinary user chats."""
    limit = _clamp(limit, DEFAULT_SEARCH_LIMIT, MAX_SEARCH_LIMIT)
    results = search_conversation_transcripts(
        query,
        exclude_conversation_id=exclude_conversation_id,
        limit=limit,
    )
    return {"query": query, "results": results}


# ---------------------------------------------------------------------------
# Desktop conversation drawer (user-facing full-dialogue search)
# ---------------------------------------------------------------------------
# The drawer lists every chat the user can open, so its search answers "which of
# my conversations mention X" over the whole visible transcript.  It differs
# from the agent-facing search above in exactly two ways: the conversation on
# screen is included (the user can already read it), and the payload is
# projected down to what a result row needs — no scores, no counts — so a
# response cannot grow with transcript size.
BROWSER_SEARCH_DEFAULT_LIMIT = 10
BROWSER_SEARCH_MAX_LIMIT = 25
BROWSER_MAX_SNIPPETS_PER_CONVERSATION = 3
BROWSER_MAX_SNIPPET_CHARS = 240
BROWSER_MAX_QUERY_CHARS = 200


def _browser_snippet(snippet: Dict[str, Any]) -> Dict[str, Any]:
    """One bounded match excerpt: transcript position, role and clipped text."""
    text = snippet.get("text") or ""
    if len(text) > BROWSER_MAX_SNIPPET_CHARS:
        text = text[: BROWSER_MAX_SNIPPET_CHARS - 1] + "…"
    return {
        "position": snippet.get("position"),
        "role": snippet.get("role"),
        "text": text,
    }


def search_conversation_history_for_browser(
    query: str,
    limit: int = BROWSER_SEARCH_DEFAULT_LIMIT,
) -> Dict[str, Any]:
    """Full-transcript search shaped for the desktop history drawer.

    Includes the conversation currently on screen: the drawer searches history
    the user can already open, unlike the agent-facing search which must not
    see the chat that is asking.  Private agent transcripts (subagent/worker
    chats and any child conversation) stay out, per the shared scope rules.

    Each result is ``{"conversation_id", "title", "timestamp", "snippets"}``
    with snippets capped in number and length; ranking internals (``score``,
    ``match_count``) never leave the gateway.
    """
    text = (query or "").strip()[:BROWSER_MAX_QUERY_CHARS]
    if not text:
        return {"query": text, "results": []}
    limit = _clamp(limit, BROWSER_SEARCH_DEFAULT_LIMIT, BROWSER_SEARCH_MAX_LIMIT)

    hits = search_conversation_transcripts(
        text,
        exclude_conversation_id=None,
        limit=limit,
    )
    results = []
    for hit in hits[:limit]:
        snippets = (hit.get("snippets") or [])[:BROWSER_MAX_SNIPPETS_PER_CONVERSATION]
        results.append({
            "conversation_id": hit.get("conversation_id"),
            "title": hit.get("title") or "Untitled",
            "timestamp": hit.get("updated_at") or hit.get("created_at") or "",
            "snippets": [_browser_snippet(snippet) for snippet in snippets],
        })
    return {"query": text, "results": results}
