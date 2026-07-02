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
`remember` tool during the session. These SKIP discovery (you don't need to find them, they're \
given) but do NOT get a free pass — judge them exactly as strictly as anything you discover \
yourself. The agent calling `remember` is not infallible: it may be overconfident, may be saving \
something ephemeral, or may be duplicating something already known.

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

## The no-op default
Silence is CORRECT and PREFERRED. Most sessions produce nothing — this applies just as much when
there are nominated candidates as when there aren't; a nomination is a request to consider, not an
instruction to save. Before including anything, ask: "would a future agent plausibly act better
because of this, versus just re-deriving it in-situ?" If the answer is no or you're unsure, leave
it out. Do not pad the output to seem useful.

## Duplicates
You will be shown existing memories that might overlap with each nominated candidate. If a
candidate restates or refines an existing memory, set "duplicate_of" to that memory's id (it will
be updated in place) instead of creating a new, separate entry. If a nominated candidate explicitly
named an existing memory_id to update, treat that as the agent's own explicit intent and prefer
honoring it (as "duplicate_of") unless you're rejecting the candidate entirely.

## Output format
Return ONLY a JSON object: {"memories": [...]}. Each item:
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


def build_extraction_user_prompt(
    transcript_text: str,
    nominated: Optional[List[Dict[str, Any]]] = None,
    similar_by_nomination: Optional[List[List[Dict[str, Any]]]] = None,
) -> str:
    """Build the combined user prompt: transcript + nominated candidates
    (each paired with any similar existing memories found for it)."""
    parts = [
        "Here is the session transcript (user/assistant/tool messages, truncated if very long).",
        "--- TRANSCRIPT START ---",
        transcript_text,
        "--- TRANSCRIPT END ---",
        "",
    ]

    nominated = nominated or []
    if not nominated:
        parts.append("No candidates were explicitly nominated via `remember` this session — "
                     "discover from the transcript only.")
    else:
        parts.append(f"{len(nominated)} candidate(s) were explicitly nominated via `remember` during this session:")
        for i, cand in enumerate(nominated):
            parts.append(f"\nNominated candidate #{i + 1}:")
            parts.append(json.dumps(cand, indent=2))
            similar = (similar_by_nomination or [[]] * len(nominated))[i] if similar_by_nomination else []
            if similar:
                parts.append("Existing memories that might be related/duplicates:")
                parts.append(json.dumps(similar, indent=2))

    return "\n".join(parts)
