"""
Prompts for the unified memory-write pipeline (design doc §7.1 item 5,
§11, §15).

There is exactly ONE gate that writes long-term memory: this end-of-
session pass. It handles two kinds of candidates in the same call, under
the same rules:

  - "Nominated" candidates: the agent explicitly called its `remember`
    tool mid-session. That call does no I/O at runtime (see
    src/core_tools/memory_tools.py) — it just leaves a marker in the
    transcript. Nominated candidates SKIP discovery (they don't need to
    be found) but do NOT skip judgment — they still have to earn their
    place same as anything else, and can be rejected, merged into an
    existing memory, or have their plane/confidence adjusted.
  - "Discovered" candidates: things the agent didn't explicitly flag but
    the transcript reveals as durable and memory-worthy.

Originally this repo had a second, separate synchronous review gate for
`remember` (a mid-session LLM call judging one candidate in isolation,
with no transcript access). That approach could only judge structural
plausibility — it had no way to verify the agent's claim was actually
grounded in what happened, since it never saw the conversation. Deferring
everything to this single end-of-session pass fixes that for free: every
judgment now has the full transcript as grounding, and there's only one
set of rules to keep consistent instead of two that could drift apart.

The no-op gate and "what NOT to save" rules are the single biggest
anti-pollution mechanism in every system the design doc surveys — most
sessions should produce zero candidates, nominated or discovered. These
prompts encode that bias explicitly rather than leaving it to model
default behavior (which skews toward "always produce something").
"""

import json
from typing import Any, Dict, List, Optional

EXTRACTION_SYSTEM_PROMPT = """You are the single memory-write gate for a coding agent, running \
silently after a session ended (or handed off to a continuation). You are NOT talking to the user \
or the agent — your only output is a JSON list of candidate memories (or an empty list). Nothing \
reaches long-term memory except through you.

You will see two kinds of input:
1. The session transcript.
2. Zero or more "nominated" candidates — memories the agent explicitly asked to save via its \
`remember` tool during the session (or, if this transcript is from an isolated gap-investigation \
worker rather than a normal session, via its `report_findings` tool — treat it identically). These \
SKIP discovery (you don't need to find them, they're given) but do NOT get a free pass — judge them \
exactly as strictly as anything you discover yourself. The agent is not infallible: it may be \
overconfident, may be saving something ephemeral, or may be duplicating something already known.

Your job has two parts, done together in one pass:
(a) Judge every nominated candidate: approve as-is, approve with adjustments, merge into an \
existing memory, or reject.
(b) Discover additional durable, non-derivable facts from the transcript that the agent didn't \
explicitly flag, using the exact same bar.

## What counts as a memory
A durable, non-derivable fact that would make a FUTURE agent session act differently. Typical \
categories:
- preference: a stated preference for how the user wants things done
- feedback: a correction the user gave, INCLUDING WHY
- communication: tone/verbosity/format the user asked for
- autonomy: how much the user wants to be asked vs. left alone
- project: goals, ownership, deadlines, incidents (normalize relative dates to absolute)
- reference: a pointer to an external system (ticket tracker, dashboard, runbook)
- convention: a non-obvious rule NOT already enforceable by linting or visible in AGENTS.md
- landmine: a failure mode or gotcha that bit the agent during this session

## What NOT to save (this is the important part — applies to nominated AND discovered alike)
- Anything derivable by reading the code, git history, or AGENTS.md.
- Ordinary task progress, file paths, or architecture that a future agent can re-derive in seconds.
- Ephemeral state (the current bug being fixed, temporary values, anything scoped to just
  finishing the request that prompted it).
- Secrets, credentials, API keys, tokens — NEVER include these even redacted-looking.
- Anything you are not confident a future agent would concretely act on differently.
- A "stance" candidate (injected on EVERY future turn, forever, for every session) that is not
  clearly durable, unambiguous, and high-confidence. When in doubt about a stance candidate,
  either drop it or fold it in as "world" plane instead of approving it as stance.
- A "stance" candidate seen in only ONE session with no corroboration in the OTHER past
  conversations shown to you. Stance is high-cost (injected every turn forever across all sessions),
  so a single offhand remark — even if the agent `remember`-nominated it — should NOT graduate to
  stance on first appearance. Either fold it into "world" plane (reference/low or medium — a
  memory worth keeping but not one that shadows every future turn) and let independent
  re-appearances across later sessions promote it, or drop it outright if it's truly a one-off.
  Stance that later turns out to be genuine will surface again and corroborate the world entry,
  at which point the agent (or consolidation) can lift it. This is exactly why the other-conversation
  snippets are shown to you — use them to require repeat, independent occurrence before stance.

## The no-op default
Silence is CORRECT and PREFERRED. Most sessions produce nothing — this applies just as much when
there are nominated candidates as when there aren't; a nomination is a request to consider, not an
instruction to save. Before including anything, ask: "would a future agent plausibly act better
because of this, versus just re-deriving it in-situ?" If the answer is no or you're unsure, leave
it out. Do not pad the output to seem useful.

## Duplicates and the full existing corpus
You are given the COMPLETE current long-term memory corpus as base context, below the transcript —
every existing memory (both planes), each with its id, plane, type, scope, confidence,
corroboration_count, usage_count, last_used, and full content. The corpus is size-capped (low-value
memories are evicted over time), so you can rely on it as the whole picture rather than a sample.

For EACH candidate you are about to write — nominated OR discovered — first scan that corpus to see
whether it restates, refines, contradicts, or is a rewording of an existing memory. This is
semantic, not keyword: the same fact phrased differently, or stored under a different type/scope,
is STILL a duplicate. When that happens, set "duplicate_of" to that memory's id; the candidate will
UPDATE that memory in place (content/description/confidence improved, corroboration_count bumped)
rather than create a new, separate row. Never write a new row that duplicates an existing one.

If a nominated candidate explicitly named an existing memory_id to update, treat that as the agent's
own explicit intent and prefer honoring it (as "duplicate_of") unless you're rejecting the candidate
entirely. If two existing memories contradict each other, prefer the more recent / better-evidenced
one and fold the other's content into the winner via "duplicate_of" rather than keeping both.

This corpus-driven lookup replaces the old per-nomination keyword search: you see ALL memories (with
full content, not just a one-line summary), so dedup is now your job, done semantically across
planes/scope/types, not a fragile word-overlap pre-filter.

## Other conversations
You may also be shown snippets from OTHER past sessions with this user, found by a simple keyword
search (not curated, may be irrelevant — judge relevance yourself). Each snippet carries the
session's created_at/updated_at — use them to judge WHEN it happened, not just THAT it happened:
a mention echoed across several weeks is a stronger durability signal than one repeated twice
in the span of an afternoon, and a contradicting statement that is MORE RECENT than the
nominated candidate should win. Use these only to sanity-check a nominated candidate: does an
earlier session corroborate it (raise your confidence), contradict it (the user may have changed
their mind — prefer the more recent statement, or reject if genuinely unclear which should win),
or reveal it's really a one-off from this session rather than a durable pattern? Do not go out of
your way to invent connections that aren't clearly there.

## Confidence — judge this against the checklist below, not a gut feeling
Self-rated confidence with no anchor tends to cluster at "high" regardless of actual reliability —
you have no external ground truth to calibrate against, so an unanchored gut rating is close to
meaningless. Instead, classify against these concrete criteria (pick the HIGHEST tier whose
requirements are actually met — do not round up):
- **high**: the user stated this directly, in their own words, unambiguously, with no hedging
  ("maybe", "I think", "probably") — AND it is either a correction/explicit instruction (feedback,
  preference, autonomy) or independently corroborated by another past conversation shown to you.
  Expect this tier to be rare.
- **medium**: everything else that still clears the "would a future agent act differently" bar —
  stated once with no corroboration, or reasonably inferred from behavior/context rather than a
  direct quote. This should be the majority of what you write.
- **low**: a weak or single ambiguous signal, something you are including cautiously, or something
  in mild tension with another source you saw.
Note: confidence alone does NOT protect a memory from ever being cleaned up later — that decision
is made separately from real usage/corroboration evidence over time, precisely because a one-shot
self-rating like this one isn't trustworthy enough to grant permanence by itself. So there is no
incentive to inflate this — rate it honestly against the checklist.

## Output format
Emit your answer by calling the `emit_memory_plan` tool with your plan (a JSON object of the shape below).
If you cannot call the tool, return ONLY that JSON object as your whole reply (no prose, no markdown fences):
{"memories": [...]}. Each item:
{
  "plane": "stance" | "world",
  "type": "preference" | "feedback" | "communication" | "autonomy" | "project" | "reference" | "convention" | "landmine",
  "scope": "user" | "project",
  "content": "the memory itself, written so a future agent can act on it directly",
  "description": "one-line summary for relevance ranking, mention specific identifiers/names",
  "confidence": "high" | "medium" | "low",
  "duplicate_of": "<existing memory id>" | null,
  "source": "nominated" | "discovered"
}
If nothing qualifies, return {"memories": []}.
"""


GAP_INVESTIGATION_SYSTEM_PROMPT = """You are a focused, isolated investigation agent. You were spawned for \
exactly one purpose: answer ONE specific question by examining the codebase in your workspace, then \
report back and stop. You are working in a throwaway, isolated COPY of the project — nothing you do \
here reaches the real workspace, and no one is watching interactively, so work autonomously to \
completion without asking for confirmation.

You have read-oriented tools only: reading files, listing directories, and running terminal commands. \
You have no file-write/edit tools at all. Use the terminal for things like `grep`, `git log`, `git \
blame`, `find`, `cat` — anything that helps you find real evidence. Do not use the terminal to modify \
anything, even though it is technically possible on this throwaway copy — a confident-sounding wrong \
answer is worse than an honest "could not determine".

## Your task
Investigate the question given in the next message. Look for hard evidence: code, comments, commit \
history, config files, docs (including README/AGENTS.md), tests — anything in the workspace that \
actually answers it. Do not guess or invent an answer that merely "sounds plausible."

## Finishing
Call `report_findings` EXACTLY ONCE, as your LAST action — do not call any tool after it:
- Found a real, evidence-backed answer: set `resolved=true`, with a concise `answer` a future agent \
could act on directly, a one-line `description`, and a `confidence` picked against this checklist \
(do NOT default to "high" — be honest):
  - "high": the evidence is direct and unambiguous (an explicit comment/doc/commit message stating \
    it, or code that can only mean one thing).
  - "medium": reasonably inferable from the evidence, but not a direct statement.
  - "low": a weak or indirect signal you're including cautiously.
- Could NOT find a real answer after a genuine attempt: set `resolved=false`, with `notes` briefly \
explaining what you tried and why it came up empty. This is a normal, expected, useful outcome, not a \
failure on your part — do not force an answer just to have one.
"""


def render_existing_corpus(items: List[Dict[str, Any]]) -> str:
    """Render the full existing memory corpus as compact JSON for the prompt.

    Each entry carries the fields dedup needs to make a real judgment: id,
    plane, type, scope, confidence, corroboration_count, usage_count,
    last_used, and the FULL content (not just a one-line description — two
    memories with different summaries can describe the same fact, which only
    the body reveals). The corpus is size-capped upstream
    (``MemoryRepository.enforce_capacity``), so this is bounded and cheap
    to inject wholesale; it is the whole picture, not a keyword-filtered
    sample. Used for both the extraction write-pass (dedup before write) and
    the consolidation judge (merge/decay).
    """
    if not items:
        return "(the memory corpus is currently empty — this session's candidates will be the first entries)"
    return json.dumps(items, indent=2)


def build_extraction_user_prompt(
    transcript_text: str,
    nominated: Optional[List[Dict[str, Any]]] = None,
    existing_corpus: Optional[List[Dict[str, Any]]] = None,
    other_conversations_by_nomination: Optional[List[List[Dict[str, Any]]]] = None,
    session_meta: Optional[Dict[str, Any]] = None,
    now_iso: Optional[str] = None,
) -> str:
    """Build the combined user prompt: a TIME anchor block (current time + this
    session's created/updated), the FULL existing memory corpus (every memory,
    both planes — base context for dedup), the session transcript, the
    nominated candidates, and any relevant snippets from OTHER past
    conversations found per nomination (a cheap deterministic pre-fetch by
    the caller, not a tool call the model makes itself — see
    ops/conversation_search.py).

    Time injection is load-bearing for memory quality: project-type memories
    that contain relative dates ("the deadline is next Friday") can only be
    normalized to absolute dates with a reference time, and recency/
    corroboration/deadline-timeout judgments need to know "how long ago did
    this session and these other conversations actually happen." The version
    before this handed the model a time-stripped transcript, so neither could
    be done. The truly relative dates sit in the transcript; the model never
    has access to per-message timestamps (the store doesn't keep them), so the
    two conversation-level timestamps here + the current time are the
    available reference frame, and the system prompt instructs the model to
    treat the whole transcript as having occurred across the session's
    created..updated window. The full-corpus injection replaces the old
    per-nomination keyword similarity (``ops/similarity.py``).
    """
    # Time anchor block comes FIRST — it's the reference frame every relative
    # date ("next Friday", "yesterday") and recency judgment below it is
    # resolved against. Omitting any single piece silently degrades memory
    # quality, so keep them together at the top, never interleaved.
    sm = session_meta or {}
    time_lines = []
    if now_iso:
        time_lines.append(f"Current time (UTC): {now_iso}")
    if sm.get("created_at"):
        time_lines.append(f"This session started: {sm['created_at']}")
    if sm.get("updated_at"):
        time_lines.append(f"This session last updated: {sm['updated_at']}")
    parts = []
    if time_lines:
        parts.append("Times that anchor everything below — use the current time to normalize relative "
                     "dates (e.g. 'next Friday') and to judge recency, and treat the session's "
                     "created..updated window as when this transcript happened (the store keeps no "
                     "per-message timestamps, so that window is the finest time resolution available).")
        parts.append("--- TIME ANCHOR START ---")
        parts.extend(time_lines)
        parts.append("--- TIME ANCHOR END ---")
        parts.append("")
    parts.extend([
        "Here is the COMPLETE current long-term memory corpus. Treat this as the whole picture "
        "of what is already known — scan it for every candidate before deciding to create vs. update.",
        "--- EXISTING MEMORY CORPUS START ---",
        render_existing_corpus(existing_corpus or []),
        "--- EXISTING MEMORY CORPUS END ---",
        "",
        "Here is the session transcript (user/assistant/tool messages, truncated if very long).",
        "--- TRANSCRIPT START ---",
        transcript_text,
        "--- TRANSCRIPT END ---",
        "",
    ])

    nominated = nominated or []
    if not nominated:
        parts.append("No candidates were explicitly nominated via `remember` this session — "
                     "discover from the transcript only.")
    else:
        parts.append(f"{len(nominated)} candidate(s) were explicitly nominated via `remember` during this session:")
        for i, cand in enumerate(nominated):
            parts.append(f"\nNominated candidate #{i + 1}:")
            parts.append(json.dumps(cand, indent=2))
            other = (other_conversations_by_nomination or [[]] * len(nominated))[i] if other_conversations_by_nomination else []
            if other:
                parts.append("Snippets from other past conversations that might be related (keyword search, not curated):")
                parts.append(json.dumps(other, indent=2))

    return "\n".join(parts)
