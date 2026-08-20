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

Before the (single) LLM call, the model is handed the COMPLETE current
memory corpus as base context — every existing memory (both planes), each
with its full content — so dedup is the model's own job, done semantically
across all planes/types/scope rather than a fragile word-overlap pre-filter
(the old ``ops/similarity.py`` per-nomination shortlist that silently
missed rewordings and cross-group duplicates). The corpus stays bounded
because ``MemoryRepository.enforce_capacity`` caps total count
(see ``settings.max_memories``). A cheap deterministic pre-fetch of
relevant snippets from OTHER past conversations
(``ops/conversation_search.py``) is also done while building the prompt —
not a tool call the model itself decides to make. This still runs entirely
inside the gateway process: no tool access, no sandbox, no agent loop.
Safe by construction — this is exactly why it doesn't need the isolated
worker container that Layer 2b (Gap Engine) needs.

Triggered from ``gateway/streaming.py`` both when a top-level user_chat
conversation reaches a terminal status, AND at the moment a conversation
hands off via ``continue_as_new_chat`` (that segment is "done" from a
memory point of view even though the logical task continues elsewhere —
otherwise any ``remember`` calls made before the handoff would never be
mined). That trigger fires after EVERY qualifying turn, not once when a
conversation is truly finished — so ``run_extraction`` only scans the
messages added since that conversation's last pass (see its docstring),
rather than re-scanning the whole transcript from turn one every time.

## Layer 2b (Gap Engine) reuses this same gate

A gap-investigation worker's ``report_findings`` tool call (see
``src/core_tools/memory_tools.py`` and ``src/tool_definitions.py``) is
treated as a THIRD kind of nomination, structurally identical to
``remember`` — it does no I/O either, it just leaves a marker in ITS
transcript. ``memory/ops/dispatcher.py`` hands that transcript to
``run_gap_investigation_extraction`` below, which shares the exact same
corpus-based dedup / cross-conversation search / LLM judgment / write core
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
from gateway.task_instruction_display import (
    TASK_INSTRUCTION_START,
    extract_task_instruction,
    strip_task_instruction,
)
from memory.settings import passive_extraction_enabled
from memory.schema import MemoryItem, MEMORY_PLANES, MEMORY_TYPES
from memory.store import get_repository
from memory.ops.prompts import EXTRACTION_SYSTEM_PROMPT, build_extraction_user_prompt
from memory.ops.judge_io import (
    call_judge, EXTRACTION_PLAN_TOOL, EXTRACTION_PLAN_SCHEMA,
)
from memory.ops.conversation_search import search_conversations
from memory.ops.similarity import tokens

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 20_000
MIN_MESSAGES_TO_BOTHER = 4  # skip trivial 1-2 turn conversations, UNLESS something was nominated
EXTRACTION_MAX_TOKENS = 3072
OTHER_CONVERSATIONS_PER_NOMINATION_LIMIT = 3

# Tool calls treated as a "nomination" when scanning a transcript — see
# module docstring "Layer 2b reuses this same gate". Both leave a marker
# in the transcript and do no I/O at call time.
NOMINATION_TOOL_NAMES = ("remember", "report_findings")

# Session wrappers the write-pass must not treat as user-stated standing rules.
HANDOFF_PREFIX = "[Continued from previous agent session]"
_ECHO_CONTAINMENT = 0.5
_SENTENCE_RE = re.compile(r"[^\n.!?]+")


def _is_handoff(content: str) -> bool:
    return bool(content) and content.lstrip().startswith(HANDOFF_PREFIX)


def _split_user_content(content: str) -> Tuple[str, str]:
    """Return ``(scaffold_text, genuine_user_text)`` for one user message."""
    if not content:
        return "", ""
    if _is_handoff(content):
        return content.strip(), ""
    if TASK_INSTRUCTION_START in content:
        return (extract_task_instruction(content) or "").strip(), strip_task_instruction(content)
    return "", content.strip()


def _collect_scaffold_and_genuine(messages: List[Dict[str, Any]]) -> Tuple[str, str]:
    scaffolds: List[str] = []
    genuines: List[str] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        scaffold, genuine = _split_user_content(msg.get("content") or "")
        if scaffold:
            scaffolds.append(scaffold)
        if genuine:
            genuines.append(genuine)
    return "\n".join(scaffolds), "\n".join(genuines)


def _render_user_for_extraction(content: str) -> List[str]:
    """Omit task-instruction / handoff bodies from the judge transcript."""
    scaffold, genuine = _split_user_content(content)
    lines: List[str] = []
    if _is_handoff(content):
        lines.append(
            "HANDOFF: agent-authored one-shot session context "
            "(not a user standing rule; do not mine preferences from it)."
        )
    if genuine:
        lines.append(f"USER: {genuine}")
    elif scaffold and not _is_handoff(content):
        lines.append("USER: (session task instruction only — not a user request)")
    return lines


def _token_containment(candidate: str, haystack: str) -> float:
    cand, hay = tokens(candidate), tokens(haystack)
    if not cand:
        return 0.0
    return len(cand & hay) / len(cand)


def _is_scaffold_echo(candidate_text: str, scaffold: str, genuine: str) -> bool:
    """True when the candidate restates scaffold and the user did not also say it.

    Stripping wrappers from the transcript is the main fix. This gate is
    only for leftover paths the strip cannot see: a mid-session ``remember``
    nomination of the same text, or a later incremental turn whose judge
    slice no longer includes the wrapped first message.
    """
    if not candidate_text.strip() or not scaffold.strip():
        return False
    if _token_containment(candidate_text, genuine) >= _ECHO_CONTAINMENT:
        return False
    if _token_containment(candidate_text, scaffold) >= _ECHO_CONTAINMENT:
        return True
    for raw in _SENTENCE_RE.findall(scaffold):
        sent = raw.strip()
        if len(sent) >= 8 and _token_containment(candidate_text, sent) >= _ECHO_CONTAINMENT:
            return True
    return False


def _transcript_to_text(messages: List[Dict[str, Any]], max_chars: int = MAX_TRANSCRIPT_CHARS) -> str:
    """Render a compact, role-tagged transcript for the extraction prompt.

    TIME: deliberately per-message-time-stripped. The conversation store keeps
    no per-message timestamps, so rather than fabricate resolution the model
    doesn't have, the session's created..updated window is handed separately at
    the top of the user prompt (build_extraction_user_prompt) and the system
    prompt instructs the model to treat the whole transcript as occurring
    across that window. Adding fake zs timestamps here would mislead; adding
    nothing at all (the old behavior) left zero time resolution. This is the
    honest middle.

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
                lines.extend(_render_user_for_extraction(content))
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
    """Run the unified write pass for the messages added since this
    conversation's LAST write pass — not the whole transcript from turn
    one every time.

    ## Why incremental, not "rescan everything" (see gateway/streaming.py
    ## module docstring "Passive Memory Distillation")

    ``_schedule_memory_distillation`` fires after EVERY qualifying turn of
    a conversation, not once when the user is truly done with it — a
    10-turn conversation calls this 10 times. The original version handed
    the FULL message list to the write pass every single time, so a
    `remember` call from turn 1 got re-parsed, re-judged by the LLM, and
    re-submitted to dedup/consolidation on turns 2 through 10 too — safe
    (existing-memory dedup mostly absorbs it into a `duplicate_of` update
    rather than a fresh row) but wastes an LLM call proportional to
    (transcript length × turn count), not just transcript length. A
    memory/store.py-backed checkpoint (``get_extraction_checkpoint`` /
    ``set_extraction_checkpoint``) now tracks how many messages of this
    conversation have already been scanned, so each call only looks at
    this turn's new messages.

    Tradeoff accepted deliberately: the transcript text and nomination
    parsing the LLM judge sees is now scoped to just the new tail, not the
    whole conversation-so-far — so a nomination that only makes sense
    combined with much earlier turns (rare) won't have that context
    inline. Cross-conversation search (``ops/conversation_search.py``)
    still runs unchanged and is unaffected (it was always about OTHER
    conversations, never this one's own earlier turns).

    ## Concurrency: the checkpoint is reserved BEFORE the LLM call, not after

    ``_run_session_end_memory_ops`` (gateway/streaming.py) submits this to
    a background thread pool rather than awaiting it — so on a fast resend
    (cancel old stream, immediately start a new one for the SAME
    conversation_id — see ``_cancel_active_stream``'s "no stream ever
    outlives a cancel call" guarantee), the OLD stream's extraction call
    can still be waiting on a slow LLM response when the NEW stream
    finishes and submits its OWN extraction call for the same
    conversation_id. If the checkpoint were only advanced after a
    successful LLM round-trip, both calls could read the same starting
    checkpoint and re-scan (and potentially double-write) the same
    messages. Advancing the checkpoint to ``len(messages)`` immediately —
    before the network call — means the second call always sees the
    range as already claimed, at the cost of never retrying that range if
    THIS call then fails. That's the same tradeoff already documented
    below for plain failures, just applied slightly earlier.

    Returns the list of newly-written/updated memory ids (empty list is
    the expected common case — the no-op gate is "allowed and preferred",
    for nominated candidates too, not just discovered ones). Never
    raises: any failure is logged and treated as a no-op, since a broken
    pass must never surface as a user-visible error for a conversation
    that already completed successfully. Nominated candidates lost to a
    failed pass (or claimed by the checkpoint but never actually judged,
    e.g. this process crashing mid-call) are not retried — a missed
    memory can usually be re-established later; that's an accepted
    tradeoff for keeping this fail-open like the rest of the memory
    system (unlike the old synchronous review gate, this pass has no
    user-facing tool-call result to report failure through anyway).

    The checkpoint is deliberately only advanced once we're actually
    about to attempt a pass — NOT when skipped by ``passive_extraction_enabled``
    or "no provider configured" below, both "never even tried" gates
    rather than "tried and it didn't work out". Those are usually
    transient/config states (memory just got enabled, or a provider just
    got configured) — permanently skipping past whatever accumulated
    while off would silently and pointlessly drop that backlog forever
    instead of just catching up on the very next turn.
    """
    if not passive_extraction_enabled():
        return []

    repo = get_repository()
    checkpoint = repo.get_extraction_checkpoint(conversation_id)
    new_messages = messages[checkpoint:]
    if not new_messages:
        return []

    cfg = get_memory_extraction_config()
    if not cfg.get("api_key") or not cfg.get("base_url"):
        logger.info("[memory-extract] No provider configured — skipping (checkpoint left unadvanced).")
        return []

    # Reserve this range now, before the slow LLM call — see "Concurrency"
    # above. Everything past this point is "we tried" territory: a
    # subsequent failure (network error, rejected judgment, malformed
    # output) leaves the checkpoint advanced anyway, same as the plain
    # failure case documented above.
    repo.set_extraction_checkpoint(conversation_id, len(messages))

    written = _run_write_pass(
        conversation_id, new_messages, nomination_label="remember",
        filter_messages=messages,
    )
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
    filter_messages: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """Shared core behind ``run_extraction`` and
    ``run_gap_investigation_extraction``: parse nominations, judge
    (nominated + discovered) in one LLM call, write approved candidates.

    ``messages`` means something different per caller: for
    ``run_gap_investigation_extraction`` it's always the worker's entire
    (one-shot, never revisited) transcript. For ``run_extraction`` it's
    only the slice since that conversation's last extraction checkpoint —
    see that function's docstring for why — so ``MIN_MESSAGES_TO_BOTHER``
    below effectively means "this turn's own new content is trivial",
    not "this conversation overall is short". ``filter_messages`` (the
    full transcript) is what the scaffold-echo gate reads, so a later
    turn cannot persist a task-instruction fact just because the
    checkpoint already consumed the wrapped first message.

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

        # Time anchors for the write-pass: the current wall-clock (the one
        # reference frame relative dates can be normalized against) and this
        # session's own created_at/updated_at (when the transcript happened —
        # the store keeps NO per-message timestamps, so the conversation-level
        # window is the finest time resolution available). For gap-
        # investigation transcripts ("gap-investigation:<id>") there's no
        # matching conversation record, so session_meta is empty and the model
        # only gets the current time, which is still far better than the
        # time-stripped transcript both passes used to hand it. Fetched lazily
        # and best-effort: a missing/corrupt conversation record degrades to
        # "current time only" rather than failing the whole pass.
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        session_meta: Dict[str, Any] = {}
        try:
            from gateway.conversation_store import store as _conv_store
            sm = _conv_store.get_conversation(conversation_id)
            if isinstance(sm, dict):
                session_meta = {
                    "created_at": sm.get("created_at", ""),
                    "updated_at": sm.get("updated_at", ""),
                }
        except Exception:
            pass  # gap-investigation transcripts and other non-conversation ids

        # The model gets the COMPLETE current corpus as base context (see
        # ops/prompts.py's build_extraction_user_prompt / render_existing_corpus),
        # not a keyword-overlap shortlist. Dedup is now the model's job done
        # semantically across all planes/types/scope, because the whole corpus
        # is bounded (MemoryRepository.enforce_capacity caps total count) and
        # every memory is shown with its FULL content — two memories with
        # different one-line summaries can describe the same fact, which only
        # the body reveals. This replaces the old per-nomination
        # find_similar_existing() (ops/similarity.py) that silently missed
        # rewordings and cross-(type/scope) duplicates.
        existing_corpus: List[Dict[str, Any]] = []
        for it in repo.all_items():
            existing_corpus.append({
                "id": it.id, "plane": it.plane, "type": it.type, "scope": it.scope,
                "description": it.description, "content": it.content,
                "confidence": it.confidence, "corroboration_count": it.corroboration_count,
                "usage_count": it.usage_count, "last_used": it.last_used, "created": it.created,
            })

        # Deterministic pre-fetch, not an agent tool call — the model never
        # decides whether/how to search, it just gets a few relevant snippets
        # from OTHER sessions alongside a nomination, so it can judge whether
        # something is actually corroborated (or contradicted) by earlier
        # history rather than trusting this session's framing alone. See
        # ops/conversation_search.py module docstring for why this is a single
        # shared, deterministic utility rather than tool access.
        other_convs_by_nomination = [
            search_conversations(cand["description"], exclude_conversation_id=conversation_id,
                                  limit=OTHER_CONVERSATIONS_PER_NOMINATION_LIMIT)
            for cand in nominated
        ]

        # Structured output via a forced tool call (call_judge → parse_judge_response,
        # see ops/judge_io.py). The configured extraction provider returns EMPTY
        # content when given response_format={"type":"json_object"} — the old
        # try/response_format/except/fallback pair never fired there (no exception
        # is raised on an empty-content success) and the write pass silently no-
        # op'd. Forced tool calls are honored by that provider (finish_reason=
        # tool_calls, JSON in tool_calls[0].function.arguments) AND degrade to a
        # plain-completion content fallback for providers that reject tools.
        user_prompt = build_extraction_user_prompt(
            transcript, nominated, existing_corpus, other_convs_by_nomination,
            session_meta=session_meta, now_iso=now_iso,
        )
        parsed = call_judge(
            client, cfg["model"], EXTRACTION_SYSTEM_PROMPT, user_prompt,
            tool_name=EXTRACTION_PLAN_TOOL, tool_schema=EXTRACTION_PLAN_SCHEMA,
            max_tokens=EXTRACTION_MAX_TOKENS,
        )
        if not parsed:
            logger.warning("[memory-extract] [%s] Could not parse model output as JSON", conversation_id[:8])
            return []

        candidates = parsed.get("memories", [])
        if not candidates:
            logger.info("[memory-extract] [%s] No-op (0 candidates, %d nominated) — expected common case",
                        conversation_id[:8], len(nominated))
            return []
        return apply_extraction_plan(
            candidates, conversation_id, nomination_label, repo,
            messages=filter_messages if filter_messages is not None else messages,
        )

    except Exception:
        logger.exception("[memory-extract] [%s] Extraction failed — treating as no-op", conversation_id[:8])
        return []


def apply_extraction_plan(
    candidates: List[Dict[str, Any]],
    conversation_id: str,
    nomination_label: str = "remember",
    repo=None,
    messages: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """Apply a parsed extraction plan (list of candidate dicts from the judge's
    ``emit_memory_plan`` call, whether the judge ran in-process via ``call_judge``
    or inside an isolated memory-maintenance worker via ``dispatch_memory_maintenance``).

    Each candidate is validated, provenance-stamped, and upserted. When
    ``messages`` is provided, task-instruction / handoff echoes the user
    never restated are dropped. The caller must have already verified the
    judge returned a parseable plan — this function does NOT make a second
    LLM call. Returns a list of ``{"id": memory_id, "source":
    "nominated"|"discovered"}`` entries for every candidate that passed
    validation. Never raises: bad candidates are skipped with a warning."""
    repo = repo or get_repository()
    scaffold, genuine = _collect_scaffold_and_genuine(messages or [])
    written: List[Dict[str, str]] = []
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        plane = cand.get("plane")
        mtype = cand.get("type")
        if plane not in MEMORY_PLANES:
            logger.warning("[memory-extract] [%s] Skipping candidate with invalid plane %r", conversation_id[:8], plane)
            continue
        if mtype not in MEMORY_TYPES:
            logger.warning("[memory-extract] [%s] Skipping candidate with invalid type %r", conversation_id[:8], mtype)
            continue
        echo_hay = f"{cand.get('description', '')} {cand.get('content', '')}"
        if scaffold and _is_scaffold_echo(echo_hay, scaffold, genuine):
            logger.info(
                "[memory-extract] [%s] Skipping scaffold-echo candidate %r",
                conversation_id[:8], cand.get("description"),
            )
            continue

        source = cand.get("source", "discovered")

        provenance = (
            f"agent-driven write pass — nominated: {nomination_label}, judged together with "
            f"all other candidates from the same session under the full corpus and transcript "
            f"context (session {conversation_id[:8]}). conversation_scope: all messages "
            f"(write pass runs per-conversation, not per-message)."
        )
        if source == "nominated":
            provenance += " Source: agent explicitly nominated this via a tool call during the session."
        else:
            provenance += " Source: discovered by the model during the write pass."

        kwargs: Dict[str, Any] = {
            "content": cand["content"],
            "description": cand["description"],
            "plane": plane,
            "type": mtype,
            "scope": cand.get("scope", "project"),
            "confidence": cand.get("confidence", "medium"),
            "provenance": provenance,
        }
        if cand.get("volatile"):
            kwargs["volatile"] = True
        if cand.get("ttl_days"):
            kwargs["ttl_days"] = int(cand["ttl_days"])

        memory_id = cand.get("memory_id")
        if memory_id:
            kwargs["id"] = memory_id

        supersedes = cand.get("duplicate_of")
        if supersedes:
            existing = repo.get(supersedes)
            if existing:
                kwargs.update({
                    "usage_count": existing.usage_count,
                    "last_used": existing.last_used,
                    "created": existing.created,
                    "corroboration_count": existing.corroboration_count + 1,
                    "id": supersedes,
                })
            else:
                logger.warning(
                    "[memory-extract] [%s] duplicate_of references unknown id %s — treating as new memory",
                    conversation_id[:8], supersedes,
                )
        try:
            saved = repo.upsert(MemoryItem(**kwargs))
        except Exception:
            logger.warning("[memory-extract] [%s] Skipping invalid candidate", conversation_id[:8], exc_info=True)
            continue
        written.append({"id": saved.id, "source": source})
    return written
