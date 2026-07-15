"""
Unified memory-write pass (Layer 2a) — design doc §11 "Passive (async,
post-session)" / Codex Phase 1, merged with the "Active (in-turn)" path.

This is the ONLY place memory gets written from agent-driven activity.
Two kinds of candidates are judged together, in one LLM call, under one
set of rules (see ``ops/prompts.py`` module docstring for the reasoning):

  - "Nominated": the agent called its ``remember`` tool mid-session. That
    tool does no I/O at runtime (see ``src/core_tools/memory_tools.py``)
    — it just leaves a marker in the transcript. This pass parses those
    calls back out (``_extract_nominated_candidates``) and judges them
    with full transcript context, which a synchronous mid-session review
    could never have.
  - "Discovered": things the transcript reveals that the agent didn't
    explicitly flag.

For each nominated candidate, two cheap deterministic pre-fetches are
done before the (single) LLM call: similar existing memories
(``ops/similarity.py``) and relevant snippets from OTHER past
conversations (``ops/conversation_search.py``). Both are plain function
calls the caller makes while building the prompt — not tool calls the
model itself decides to make. This still runs entirely inside the
gateway process: no tool access, no sandbox, no agent loop. Safe by
construction — this is exactly why it doesn't need the isolated worker
container that Layer 2b (Gap Engine) needs.

Triggered from ``gateway/streaming.py`` both when a top-level user_chat
conversation reaches a terminal status, AND at the moment a conversation
hands off via ``continue_as_new_chat`` (that segment is "done" from a
memory point of view even though the logical task continues elsewhere —
otherwise any ``remember`` calls made before the handoff would never be
mined).

## Layer 2b (Gap Engine) reuses this same gate

A gap-investigation worker's ``report_findings`` tool call (see
``src/core_tools/memory_tools.py`` and ``src/tool_definitions.py``) is
treated as a THIRD kind of nomination, structurally identical to
``remember`` — it does no I/O either, it just leaves a marker in ITS
transcript. ``memory/ops/dispatcher.py`` hands that transcript to
``run_gap_investigation_extraction`` below, which shares the exact same
dedup search / cross-conversation search / LLM judgment / write core
(``_run_write_pass``) as the normal session-end path. This is deliberate
— the Gap Engine is "another source of memory candidates", not a
separate write path with its own rules; see the design doc §13 and
``docs/memory-implementation-summary.md`` for why. The only difference
is the label used in provenance text and the fact that a gap-
investigation transcript nominates at most one candidate.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from gateway.provider_registry import get_memory_extraction_config
from memory.settings import passive_extraction_enabled
from memory.schema import MemoryItem, MEMORY_PLANES, MEMORY_TYPES
from memory.store import get_repository
from memory.ops.prompts import EXTRACTION_SYSTEM_PROMPT, build_extraction_user_prompt
from memory.ops.similarity import find_similar_existing
from memory.ops.conversation_search import search_conversations

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 20_000
MIN_MESSAGES_TO_BOTHER = 4  # skip trivial 1-2 turn conversations, UNLESS something was nominated
EXTRACTION_MAX_TOKENS = 3072
SIMILAR_PER_NOMINATION_LIMIT = 5
OTHER_CONVERSATIONS_PER_NOMINATION_LIMIT = 3

# Tool calls treated as a "nomination" when scanning a transcript — see
# module docstring "Layer 2b reuses this same gate". Both leave a marker
# in the transcript and do no I/O at call time.
NOMINATION_TOOL_NAMES = ("remember", "report_findings")


def _transcript_to_text(messages: List[Dict[str, Any]], max_chars: int = MAX_TRANSCRIPT_CHARS) -> str:
    """Render a compact, role-tagged transcript for the extraction prompt.

    Tool call arguments/results are summarized rather than included in
    full — extraction only needs the narrative (what was asked, what was
    decided, what corrections happened), not raw file contents. Nominated
    `remember` calls are rendered with their actual content (unlike other
    tool calls) since they're a direct signal of what the agent thought
    was worth keeping.
    """
    lines: List[str] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            continue
        if role == "user":
            content = (msg.get("content") or "").strip()
            if content:
                lines.append(f"USER: {content}")
        elif role == "assistant":
            content = (msg.get("content") or "").strip()
            if content:
                lines.append(f"ASSISTANT: {content}")
            for tc in msg.get("tool_calls", []) or []:
                fn = tc.get("function", {})
                name = fn.get("name", "?")
                if name == "remember":
                    args = _safe_json_loads(fn.get("arguments", "{}")) or {}
                    lines.append(f"  [called remember: {args.get('description', '?')}]")
                elif name == "report_findings":
                    args = _safe_json_loads(fn.get("arguments", "{}")) or {}
                    if args.get("resolved"):
                        lines.append(f"  [called report_findings: resolved, {args.get('description', '?')}]")
                    else:
                        lines.append(f"  [called report_findings: NOT resolved — {args.get('notes', '?')}]")
                else:
                    lines.append(f"  [called {name}]")
        elif role == "tool":
            content = (msg.get("content") or "")[:300]
            lines.append(f"  [tool result: {content}]")

    text = "\n".join(lines)
    if len(text) > max_chars:
        # Keep head (task setup) and tail (final outcome/corrections) —
        # the middle (mechanical tool-call slog) is the least useful part
        # for extraction purposes.
        half = max_chars // 2
        text = text[:half] + "\n...[truncated]...\n" + text[-half:]
    return text


def _safe_json_loads(raw: str) -> Optional[dict]:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _extract_json(raw: str) -> Optional[dict]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _extract_nominated_candidates(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse the transcript for nomination tool calls made during the
    session — either `remember` (see src/core_tools/memory_tools.py — that
    tool does no I/O at call time, it only leaves this marker) or, for an
    isolated gap-investigation transcript, `report_findings` (same
    no-I/O-at-call-time shape; see module docstring "Layer 2b reuses this
    same gate"). Malformed calls (bad JSON, missing required fields, or a
    `report_findings` call that reported `resolved=false`) are skipped
    defensively rather than raising.
    """
    nominated: List[Dict[str, Any]] = []
    last_report_findings_idx: Optional[int] = None
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            name = fn.get("name")
            if name not in NOMINATION_TOOL_NAMES:
                continue
            args = _safe_json_loads(fn.get("arguments", "{}"))
            if not args:
                continue
            if name == "report_findings":
                if not args.get("resolved") or not args.get("answer") or not args.get("description"):
                    continue
                candidate = {
                    "content": args["answer"],
                    "description": args["description"],
                    "plane": args.get("plane", "world"),
                    "type": args.get("type", "gap_resolution"),
                    "scope": args.get("scope", "project"),
                    "confidence": args.get("confidence", "medium"),
                    "explicit_update_of": None,
                }
                # The investigator is instructed to call this exactly once,
                # but if it disobeys and calls it more than once, only the
                # LAST call reflects its final answer — drop earlier ones
                # rather than nominating multiple competing candidates for
                # what is ultimately one gap.
                if last_report_findings_idx is not None:
                    nominated.pop(last_report_findings_idx)
                nominated.append(candidate)
                last_report_findings_idx = len(nominated) - 1
            else:
                if not args.get("content") or not args.get("description"):
                    continue
                nominated.append({
                    "content": args["content"],
                    "description": args["description"],
                    "plane": args.get("plane", "world"),
                    "type": args.get("type", "project"),
                    "scope": args.get("scope", "project"),
                    "confidence": args.get("confidence", "medium"),
                    "explicit_update_of": args.get("memory_id"),
                })
    return nominated


def run_extraction(conversation_id: str, messages: List[Dict[str, Any]]) -> List[str]:
    """Run the unified write pass for one finished (or handed-off)
    conversation segment.

    Returns the list of newly-written/updated memory ids (empty list is
    the expected common case — the no-op gate is "allowed and preferred",
    for nominated candidates too, not just discovered ones). Never
    raises: any failure is logged and treated as a no-op, since a broken
    pass must never surface as a user-visible error for a conversation
    that already completed successfully. Nominated candidates lost to a
    failed pass are not retried — a missed memory can usually be
    re-established later; that's an accepted tradeoff for keeping this
    fail-open like the rest of the memory system (unlike the old
    synchronous review gate, this pass has no user-facing tool-call
    result to report failure through anyway).
    """
    written = _run_write_pass(conversation_id, messages, nomination_label="remember")
    return [w["id"] for w in written]


def run_gap_investigation_extraction(gap_id: str, raw_messages: List[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    """Judge a gap-investigation worker's transcript through the exact
    same write pass as a normal session (see module docstring "Layer 2b
    reuses this same gate"). The worker's ``report_findings`` call (if
    any, and if it reported ``resolved=true``) is parsed by
    ``_extract_nominated_candidates`` exactly like a ``remember`` call
    would be.

    Returns ``(memory_id, confidence)`` for the written/updated memory if
    the finding survived judgment, or ``None`` if there was nothing to
    judge (no ``report_findings`` call, or it reported unresolved) or the
    judgment rejected it. Never raises — same fail-open contract as
    ``run_extraction``. The caller (``memory/ops/dispatcher.py``) treats
    ``None`` as "defer the gap", not as an error.
    """
    written = _run_write_pass(
        f"gap-investigation:{gap_id}",
        raw_messages,
        nomination_label="report_findings (isolated gap-investigation worker)",
    )
    for w in written:
        if w["source"] != "nominated":
            continue
        item = get_repository().get(w["id"])
        return (w["id"], item.confidence if item else "medium")
    return None


def _run_write_pass(
    conversation_id: str,
    messages: List[Dict[str, Any]],
    nomination_label: str = "remember",
) -> List[Dict[str, str]]:
    """Shared core behind ``run_extraction`` and
    ``run_gap_investigation_extraction``: parse nominations, judge
    (nominated + discovered) in one LLM call, write approved candidates.

    Returns a list of ``{"id": memory_id, "source": "nominated"|"discovered"}``
    — richer than the plain id list ``run_extraction`` exposes publicly,
    so gap investigation can pick out specifically the nominated write
    (there is at most one per gap) without guessing at list positions.
    ``nomination_label`` only affects the provenance string recorded on
    written memories — it does not change judgment behavior.
    """
    if not passive_extraction_enabled():
        return []

    nominated = _extract_nominated_candidates(messages)
    if len(messages) < MIN_MESSAGES_TO_BOTHER and not nominated:
        return []

    try:
        cfg = get_memory_extraction_config()
        if not cfg.get("api_key") or not cfg.get("base_url"):
            logger.info("[memory-extract] No provider configured — skipping.")
            return []

        client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])
        transcript = _transcript_to_text(messages)

        repo = get_repository()
        similar_by_nomination = [
            find_similar_existing(repo, plane=cand["plane"], scope=cand["scope"],
                                   description=cand["description"], limit=SIMILAR_PER_NOMINATION_LIMIT)
            for cand in nominated
        ]
        # Honor an explicit memory_id from the agent as a strong duplicate signal,
        # even if the keyword-overlap search didn't independently surface it.
        for cand, similar in zip(nominated, similar_by_nomination):
            if cand.get("explicit_update_of") and not any(s["id"] == cand["explicit_update_of"] for s in similar):
                existing = repo.get(cand["explicit_update_of"])
                if existing:
                    similar.insert(0, {"id": existing.id, "description": existing.description,
                                        "type": existing.type, "confidence": existing.confidence})

        # Deterministic pre-fetch (same shape as similar_by_nomination above),
        # not an agent tool call — the model never decides whether/how to
        # search, it just gets a few relevant snippets from OTHER sessions
        # alongside the candidate, so it can judge whether something is
        # actually corroborated (or contradicted) by earlier history rather
        # than trusting this session's framing alone. See
        # ops/conversation_search.py module docstring for why this is a
        # single shared, deterministic utility rather than tool access.
        other_convs_by_nomination = [
            search_conversations(cand["description"], exclude_conversation_id=conversation_id,
                                  limit=OTHER_CONVERSATIONS_PER_NOMINATION_LIMIT)
            for cand in nominated
        ]

        kwargs: Dict[str, Any] = dict(
            model=cfg["model"],
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": build_extraction_user_prompt(
                    transcript, nominated, similar_by_nomination, other_convs_by_nomination)},
            ],
            max_tokens=EXTRACTION_MAX_TOKENS,
            temperature=0,
        )
        try:
            response = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
        except Exception:
            # Provider may not support response_format — retry without it.
            response = client.chat.completions.create(**kwargs)

        raw = response.choices[0].message.content or ""
        parsed = _extract_json(raw)
        if not parsed:
            logger.warning("[memory-extract] [%s] Could not parse model output as JSON", conversation_id[:8])
            return []

        candidates = parsed.get("memories", [])
        if not candidates:
            logger.info("[memory-extract] [%s] No-op (0 candidates, %d nominated) — expected common case",
                        conversation_id[:8], len(nominated))
            return []

        written: List[Dict[str, str]] = []
        for cand in candidates:
            try:
                if cand.get("plane") not in MEMORY_PLANES or cand.get("type") not in MEMORY_TYPES:
                    continue
                source = cand.get("source", "discovered")
                provenance = (
                    f"agent-nominated ({nomination_label}), validated from conversation {conversation_id[:8]}"
                    if source == "nominated"
                    else f"passive-extraction, discovered from conversation {conversation_id[:8]}"
                )
                kwargs2: Dict[str, Any] = dict(
                    content=cand["content"],
                    description=cand["description"],
                    plane=cand["plane"],
                    type=cand["type"],
                    scope=cand.get("scope", "project"),
                    confidence=cand.get("confidence", "low"),
                    provenance=provenance,
                )
                if cand.get("duplicate_of"):
                    kwargs2["id"] = cand["duplicate_of"]
                    existing = repo.get(cand["duplicate_of"])
                    if existing:
                        # An in-place update must not reset the very history that
                        # decay/retention judges it by (see ops/consolidator.py) —
                        # only content/description/confidence/provenance actually
                        # change here. corroboration_count is bumped because this
                        # candidate independently resolved back to the same memory
                        # from a SEPARATE session — a deterministic, code-computed
                        # signal that a self-reported confidence can't fake.
                        kwargs2["usage_count"] = existing.usage_count
                        kwargs2["last_used"] = existing.last_used
                        kwargs2["created"] = existing.created
                        kwargs2["corroboration_count"] = existing.corroboration_count + 1
                item = MemoryItem(**kwargs2)
                repo.upsert(item)
                written.append({"id": item.id, "source": source})
            except (KeyError, ValueError) as e:
                logger.warning("[memory-extract] Skipped malformed candidate: %s", e)

        logger.info("[memory-extract] [%s] Wrote %d memor(y/ies) (%d nominated, %d total candidates)",
                    conversation_id[:8], len(written), len(nominated), len(candidates))
        return written

    except Exception:
        logger.exception("[memory-extract] [%s] Extraction failed — treating as no-op", conversation_id[:8])
        return []
