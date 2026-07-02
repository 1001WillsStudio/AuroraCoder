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

Runs entirely inside the gateway process: no tool access, no sandbox.
Safe by construction — this is exactly why it doesn't need the isolated
worker container that Layer 2b (Gap Engine) needs.

Triggered from ``gateway/streaming.py`` both when a top-level user_chat
conversation reaches a terminal status, AND at the moment a conversation
hands off via ``continue_as_new_chat`` (that segment is "done" from a
memory point of view even though the logical task continues elsewhere —
otherwise any ``remember`` calls made before the handoff would never be
mined).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI

from gateway.provider_registry import get_memory_extraction_config
from gateway.settings_store import get_other_settings
from gateway.memory.schema import MemoryItem, MEMORY_PLANES, MEMORY_TYPES
from gateway.memory.store import get_repository
from gateway.memory.ops.prompts import EXTRACTION_SYSTEM_PROMPT, build_extraction_user_prompt
from gateway.memory.ops.similarity import find_similar_existing

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 20_000
MIN_MESSAGES_TO_BOTHER = 4  # skip trivial 1-2 turn conversations, UNLESS something was nominated
EXTRACTION_MAX_TOKENS = 3072
SIMILAR_PER_NOMINATION_LIMIT = 5


def extraction_enabled() -> bool:
    mem = get_other_settings().get("memory", {})
    return bool(mem.get("passive_enabled", True))


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
    """Parse the transcript for `remember` tool calls made during the
    session (see src/core_tools/memory_tools.py — that tool does no I/O
    at call time, it only leaves this marker). Malformed calls (bad JSON,
    missing required fields) are skipped defensively rather than raising.
    """
    nominated: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            if fn.get("name") != "remember":
                continue
            args = _safe_json_loads(fn.get("arguments", "{}"))
            if not args or not args.get("content") or not args.get("description"):
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
    if not extraction_enabled():
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

        kwargs: Dict[str, Any] = dict(
            model=cfg["model"],
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": build_extraction_user_prompt(transcript, nominated, similar_by_nomination)},
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

        written: List[str] = []
        for cand in candidates:
            try:
                if cand.get("plane") not in MEMORY_PLANES or cand.get("type") not in MEMORY_TYPES:
                    continue
                source = cand.get("source", "discovered")
                provenance = (
                    f"agent-nominated (remember), validated from conversation {conversation_id[:8]}"
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
                item = MemoryItem(**kwargs2)
                repo.upsert(item)
                written.append(item.id)
            except (KeyError, ValueError) as e:
                logger.warning("[memory-extract] Skipped malformed candidate: %s", e)

        logger.info("[memory-extract] [%s] Wrote %d memor(y/ies) (%d nominated, %d total candidates)",
                    conversation_id[:8], len(written), len(nominated), len(candidates))
        return written

    except Exception:
        logger.exception("[memory-extract] [%s] Extraction failed — treating as no-op", conversation_id[:8])
        return []
