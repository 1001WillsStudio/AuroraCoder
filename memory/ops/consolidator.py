"""
Consolidation + decay (Layer 2a) — design doc §11 "Consolidate + decay":
keep the corpus tight by merging near-duplicates and retiring memories that
have gone idle or are contradicted by newer evidence.

This is now AGENT-DRIVEN, not heuristic. With the memory corpus size-capped
(see ``MemoryRepository.enforce_capacity`` / ``settings.max_memories``,
default 50 — comfortably above real usage), the WHOLE world-plane corpus is
small enough to hand to a judgment LLM as base context in one call. That
model sees every memory with its FULL content (two memories with different
one-line summaries can describe the same fact — only the body reveals that),
and decides, semantically and across types/scope:
  - MERGE: a dupe or a refinement of an existing memory folds into the
    keeper (content/description/confidence improved, the loser removed),
  - DELETE: a memory that is stale, contradicted by something newer/better
    evidenced, or simply no longer worth keeping, is retired.

This replaces the v1 keyword-overlap (``ops/similarity.py``) dedupe plus the
self-reported-confidence/grace-day heuristic decay. Both of those were honest
starting priors, but both silently failed exactly when dedup and retention
mattered most: rewordings and cross-(type/scope) duplicates had low word
overlap and slipped past jaccard, and a self-rated "high" confidence could
shield a wrong fact from the heuristic decay forever. A judge that actually
reads the memories has neither blind spot.

One deterministic pass is retained: VOLATILE + past its ``ttl_days`` is
expired by code, not the LLM. Volatile memories are time-bound by design
(design doc §10: "carries ttl, re-verify on read"); there is no
re-verify-on-read mechanism yet, so the honest behavior is to expire on
schedule rather than ask a model to opine on a date. This pass is cheap,
pure, and never wrong on the facts that matter for it.

Never touches the ``stance`` plane automatically — explicit
preferences/corrections are exactly what §15 calls "worth storing once" and
should only ever be removed by an explicit ``remember`` overwrite or a human
editing the file directly. The judge only ever receives world-plane items.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from openai import OpenAI

from gateway.provider_registry import get_memory_extraction_config
from memory.schema import MemoryItem
from memory.ops.judge_io import (
    call_judge, CONSOLIDATION_PLAN_TOOL, CONSOLIDATION_PLAN_SCHEMA,
)
from memory.settings import passive_extraction_enabled
from memory.store import MemoryRepository, get_repository

logger = logging.getLogger(__name__)

# Reuse the same provider/model the extraction write-pass uses — consolidation
# is the sibling housekeeping call, not a separate service to configure.
CONSOLIDATION_MAX_TOKENS = 3072

CONSOLIDATION_SYSTEM_PROMPT = """You are the memory consolidation housekeeping gate for a coding agent, \
running silently after a session ended. You are NOT talking to the user. Your only output is a JSON \
object describing merge and delete operations over the existing memory corpus, or an empty set if \
nothing to do.

You are given the current time (UTC) and the COMPLETE current world-plane memory corpus (stance \
memories are excluded and never your concern) as base context — every memory, each with its id, type, \
scope, description, full content, confidence, corroboration_count, usage_count, last_used, created, \
volatile, and ttl_days. This is the whole picture, not a sample. Use the current time to judge \
recency/staleness (how long since last_used / since created), volatile ttl_days already elapsed, \
and deadline-bearing memories whose date has passed — none of which can be judged from timestamps \
alone without a "now" reference.

Your job in one pass:
1. MERGE — when one memory restates, refines, rewords, or is a cross-(type/scope) duplicate of another, \
fold the loser into the keeper. Semantic, not keyword: the same fact phrased differently, or stored \
under a different type/scope, is STILL a duplicate. Pick the keeper as the one with stronger evidence \
(higher corroboration_count / usage_count / more recent last_used), keep its id, and produce improved \
combined content + description for it. Highly corroborated/used memories should win ties.
2. DELETE — retire a memory that is (a) stale: not used in a long time relative to its peers AND not \
corroborated; (b) contradicted by a newer or better-evidenced memory covering the same subject (fold \
its still-useful bits into the winner via a MERGE instead of a bare DELETE where possible); or (c) \
clearly no longer worth keeping. Be conservative: when in doubt, keep — a memory costs little and a \
wrong deletion can't be undone here.

Do NOT invent new memories. Do NOT touch stance. Do NOT delete a memory you also plan to merge FROM \
(it'll be removed by the merge). Operate only on the ids actually present in the corpus you were given.

## Output format
Emit your answer by calling the `emit_consolidation_plan` tool with your plan. If you cannot call the
tool, return ONLY a JSON object:
{"merges": [{"into": "<keeper id>", "from": "<loser id>", "content": "<new combined content>", \
"description": "<new one-line summary>", "confidence": "high"|"medium"|"low"}], "deletes": ["<id>", ...]}
If nothing to do, return {"merges": [], "deletes": []}.
"""


def _age_days(iso_ts: Optional[str]) -> float:
    if not iso_ts:
        return float("inf")
    try:
        dt = datetime.fromisoformat(iso_ts)
    except (ValueError, TypeError):
        return float("inf")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def _expire_volatile(repo: MemoryRepository) -> int:
    """Deterministic pass: expire world-plane volatile memories past their
    ``ttl_days``. Not an LLM judgment — a date is a fact, not an opinion (see
    module docstring). Returns count removed."""
    removed = 0
    for item in repo.all_items(plane="world"):
        if item.volatile and item.ttl_days is not None and _age_days(item.created) >= item.ttl_days:
            repo.delete(item.id)
            removed += 1
            logger.info("[memory-consolidate] Expired volatile memory %s (%s, ttl=%dd)",
                        item.id, item.description, item.ttl_days)
    return removed


def _build_world_corpus(repo: MemoryRepository) -> List[Dict[str, Any]]:
    """Render the full world-plane corpus for the judge (full content +
    all retention-relevant metadata). Stance is deliberately excluded."""
    corpus: List[Dict[str, Any]] = []
    for it in repo.all_items(plane="world"):
        corpus.append({
            "id": it.id, "type": it.type, "scope": it.scope,
            "description": it.description, "content": it.content,
            "confidence": it.confidence, "corroboration_count": it.corroboration_count,
            "usage_count": it.usage_count, "last_used": it.last_used, "created": it.created,
            "volatile": it.volatile, "ttl_days": it.ttl_days,
        })
    return corpus


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


def _run_consolidation_judge(
    client: OpenAI, model: str, corpus: List[Dict[str, Any]], now_iso: Optional[str] = None
) -> Dict[str, Any]:
    """Single LLM call: hand the judge the current time and the whole world
corpus, get back the merge/delete plan. Returns ``{"merges": [...],
"deletes": [...]}`` (empty on any failure — consolidation must be fail-open).
The current time is the reference frame every recency/staleness/ttl/deadline
judgment the DELETE rule needs resolves against; without it the judge can see
last_used/created/ttl_days but not "how long ago" any of them is."""
    now_line = f"Current time (UTC): {now_iso}\n\n" if now_iso else ""
    user = (
        now_line
        + "Here is the COMPLETE current world-plane memory corpus. Decide which entries should be "
        "MERGED (a loser folded into a keeper) or DELETED (retired). Be conservative; when in doubt keep.\n"
        "--- WORLD MEMORY CORPUS START ---\n"
        + json.dumps(corpus, indent=2)
        + "\n--- WORLD MEMORY CORPUS END ---"
    )
    # Structured output via a forced tool call (call_judge → parse_judge_response,
    # see ops/judge_io.py). The configured consolidation provider returns EMPTY
    # content when given response_format={"type":"json_object"} with no
    # exception raised, so the old try/response_format/except/content-fallback
    # pair silently no-op'd here. Forced tool calls are honored by that provider
    # (finish_reason=tool_calls, JSON in tool_calls[0].function.arguments) and
    # degrade to a plain-completion content fallback for providers that reject
    # tools. Consolidation must be fail-open: any unparsable result → empty plan.
    parsed = call_judge(
        client, model, CONSOLIDATION_SYSTEM_PROMPT, user,
        tool_name=CONSOLIDATION_PLAN_TOOL, tool_schema=CONSOLIDATION_PLAN_SCHEMA,
        max_tokens=CONSOLIDATION_MAX_TOKENS,
    )
    if not parsed:
        logger.warning("[memory-consolidate] Could not parse consolidation judgment as JSON")
        return {"merges": [], "deletes": []}
    return {"merges": parsed.get("merges", []) or [], "deletes": parsed.get("deletes", []) or []}


def _apply_plan(repo: MemoryRepository, plan: Dict[str, Any]) -> Dict[str, int]:
    """Apply the judge's plan. Validates every id is a real world memory the
    judge was actually shown; silently skips anything it invented or that a
    merge already consumed. Returns counts."""
    by_id = {it.id: it for it in repo.all_items(plane="world")}
    merged = 0
    deleted = 0
    consumed: set = set()
    protected: set = set()  # ids that must survive this pass — a merge's
                           # keeper (into) side must not also be deleted, or
                           # the merge is pointless; and a loser just merged
                           # away must not be re-merged or deleted either.

    for m in plan.get("merges", []):
        into_id = m.get("into")
        from_id = m.get("from")
        if not into_id or not from_id or into_id == from_id:
            continue
        if into_id not in by_id or from_id not in by_id:
            logger.warning("[memory-consolidate] Merge skipped — unknown id (into=%s from=%s)",
                           into_id, from_id)
            continue
        if from_id in consumed or from_id in protected or into_id in consumed or into_id in protected:
            # into_id in protected = a keeper of an earlier merge — folding
            # something into it would clobber that merge's already-improved
            # content; from_id in protected means it's already a keeper.
            continue
        winner = by_id[into_id]
        loser = by_id[from_id]
        content = (m.get("content") or winner.content).strip() or winner.content
        description = (m.get("description") or winner.description).strip() or winner.description
        confidence = m.get("confidence") or winner.confidence
        # Retention history must survive a merge — keep the broader/better of
        # each side, never reset to zero, and bump corroboration because the
        # loser independently resolved to the same fact.
        merged_item = MemoryItem(
            id=winner.id,
            plane=winner.plane, type=winner.type, scope=winner.scope,
            description=description, content=content, confidence=confidence,
            provenance=winner.provenance,
            created=min(winner.created, loser.created),
            last_used=max(winner.last_used or winner.created, loser.last_used or loser.created),
            usage_count=max(winner.usage_count, loser.usage_count),
            corroboration_count=winner.corroboration_count + loser.corroboration_count + 1,
            volatile=winner.volatile or loser.volatile,
            ttl_days=max(filter(None, [winner.ttl_days, loser.ttl_days]), default=None)
            if (winner.ttl_days or loser.ttl_days) else None,
            supersedes=loser.id,
        )
        repo.upsert(merged_item)
        repo.delete(from_id)
        consumed.add(from_id)
        protected.add(into_id)
        merged += 1
        logger.info("[memory-consolidate] Merged %s into %s", from_id, into_id)

    for d in plan.get("deletes", []):
        if isinstance(d, dict):
            d = d.get("id")
        if not d or d not in by_id or d in consumed or d in protected:
            continue
        repo.delete(d)
        deleted += 1
        logger.info("[memory-consolidate] Deleted %s", d)

    return {"merged": merged, "deleted": deleted}


def _merge_and_decay_world_memories() -> Dict[str, int]:
    """LLM-driven merge + decay of the world plane. Fail-open: any error
    logs and reports zero, never surfacing to the caller."""
    if not passive_extraction_enabled():
        return {"merged": 0, "deleted": 0}
    try:
        cfg = get_memory_extraction_config()
        if not cfg.get("api_key") or not cfg.get("base_url") or not cfg.get("model"):
            logger.info("[memory-consolidate] No provider configured — skipping LLM pass.")
            return {"merged": 0, "deleted": 0}
        repo = get_repository()
        corpus = _build_world_corpus(repo)
        if not corpus:
            return {"merged": 0, "deleted": 0}  # nothing to judge
        client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])
        # Same reason the write-pass injects now: every recency/staleness/ttl/
        # deadline judgment the DELETE rule makes resolves against "now", and
        # without it the judge sees timestamps but not how long ago any are.
        now_iso = datetime.now(timezone.utc).isoformat()
        plan = _run_consolidation_judge(client, cfg["model"], corpus, now_iso=now_iso)
        return _apply_plan(repo, plan)
    except Exception:
        logger.exception("[memory-consolidate] LLM consolidation pass failed")
        return {"merged": 0, "deleted": 0}


def run_consolidation() -> Dict[str, int]:
    """Run the full consolidation pass: deterministic volatile expiry, then
    the agent-driven merge + decay of the world plane. Never raises — a failed
    housekeeping pass should never surface as a user-visible error."""
    result = {"expired": 0, "merged": 0, "deleted": 0}
    try:
        repo = get_repository()
        result["expired"] = _expire_volatile(repo)
    except Exception:
        logger.exception("[memory-consolidate] Volatile expiry pass failed")
    result.update(_merge_and_decay_world_memories())
    return result
