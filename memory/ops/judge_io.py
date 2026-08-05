"""
Structured-output I/O for the two memory-judgment LLM calls — extraction
write pass (``ops/extractor.py::_run_write_pass``) and consolidation judge
(``ops/consolidator.py::_run_consolidation_judge``).

## Why this exists (the root-cause bug it fixes)

The configured gateway extraction provider returns EMPTY content
(finish_reason=stop, message.content='') when a ``chat.completions.create``
call passes ``response_format={"type": "json_object"}``. Without
``response_format`` it returns perfectly fine JSON in ``content`` — so the old
two-call try/except ("forced JSON; on exception retry without it") never fires
on this provider, because there is NO exception: the empty-content call
succeeds, the parse silently yields ``None``, and the write/consolidation pass
becomes a silent no-op. That was undetectable from the outside — the memory
system simply stopped learning from sessions.

The same provider fully supports *forced tool calls*: passing
``tools=[{type:function, function:{...}}]`` together with
``tool_choice={"type":"function","function":{"name":...}}`` returns a
``finish_reason="tool_calls"`` response with a valid JSON string in
``message.tool_calls[0].function.arguments``. Forced tool calls are the robust
structured-output transport here — they work on this provider AND on providers
that would otherwise require ``response_format``, and they degrade gracefully:
if a provider errors on a forced-tool call, ``call_judge`` falls back to a
plain completion and ``parse_judge_response`` then reads JSON out of
``content`` (the old happy path for providers that honor response_format is
implicitly preserved, since nothing asserts that path was used).

Both judges now go through ``call_judge`` → ``parse_judge_response`` here, so
the structured-output strategy lives in exactly one place and both passes get
the fix. Nothing here is memory-specific beyond the two JSON schemas; the
helpers are deliberately generic.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Tool (function-calling) names the two judges emit their plan through.
EXTRACTION_PLAN_TOOL = "emit_memory_plan"
CONSOLIDATION_PLAN_TOOL = "emit_consolidation_plan"

# ── Schemas ────────────────────────────────────────────────────────────────
# Kept in sync with memory.schema (MEMORY_PLANES/MEMORY_TYPES/MEMORY_SCOPES/
# CONFIDENCE_LEVELS) and with the JSON shape already documented in
# ops/prompts.py::EXTRACTION_SYSTEM_PROMPT and
# consolidator.py::CONSOLIDATION_SYSTEM_PROMPT. Duplicated rather than
# reflected from the dataclass on purpose — providers validate tool schemas
# eagerly, so the schema must be a plain JSON-Schema dict, not live objects.

EXTRACTION_PLAN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "plane": {"type": "string", "enum": ["stance", "world"]},
                    "type": {
                        "type": "string",
                        "enum": [
                            "preference", "feedback", "communication", "autonomy",
                            "project", "reference", "convention", "landmine",
                            "lesson", "gap_resolution",
                        ],
                    },
                    "scope": {"type": "string", "enum": ["user", "project"]},
                    "content": {"type": "string"},
                    "description": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "source": {"type": "string", "enum": ["nominated", "discovered"]},
                    "duplicate_of": {"type": ["string", "null"]},
                },
                "required": ["plane", "type", "scope", "content", "description"],
            },
        },
    },
    "required": ["memories"],
}

CONSOLIDATION_PLAN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "merges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "into": {"type": "string"},
                    "from": {"type": "string"},
                    "content": {"type": "string"},
                    "description": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["into", "from"],
            },
        },
        "deletes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["merges", "deletes"],
}


def extract_json(raw: Optional[str]) -> Optional[dict]:
    """Best-effort JSON extraction from a model's free-text ``content``.

    Tries a plain ``json.loads`` first (the common case where the model
    returned exactly JSON), then a greedy regex for the outermost ``{...}``
    (the model wrapped JSON in prose / markdown fences). Returns ``None`` (never
    raises) on any failure — callers treat ``None`` as "no plan, fail-open".

    The greedy regex is intentional: structured-output fallback content is
    expected to be a single top-level object, and a greedy ``\\{.*\\}`` matches
    the OUTERMOST braces so trailing/leading noise is stripped correctly. For
    nested objects the inner closing braces would otherwise terminate early
    under a non-greedy match.
    """
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def parse_judge_response(response: Any) -> Optional[dict]:
    """Extract the judge's plan from a chat-completion response, preferring
    a tool call (the robust structured-output path) and falling back to
    JSON parsed out of ``content`` (providers that lack / reject forced tool
    calls, or that simply ignored the tool and answered in prose).

    A single response may legally carry multiple tool calls; we honor the
    first one that yields a dict. ``function.arguments`` is allowed to be
    already-decoded (some client bindings deserialize it to a dict) or a
    JSON string (the spec / OpenAI SDK shape). Never raises.
    """
    if response is None:
        return None
    try:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return None
        msg = choices[0].message
    except Exception:
        return None

    tool_calls = getattr(msg, "tool_calls", None) or []
    for tc in tool_calls:
        try:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            args = getattr(fn, "arguments", None)
            if isinstance(args, dict):
                return args
            if isinstance(args, str):
                parsed = extract_json(args)
                if parsed is not None:
                    return parsed
        except Exception:
            continue

    content = getattr(msg, "content", None) or ""
    return extract_json(content)


def call_judge(
    client: Any,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    tool_name: str,
    tool_schema: Dict[str, Any],
    max_tokens: int,
    temperature: float = 0,
) -> Optional[dict]:
    """One structured-output LLM call, fail-open.

    Calls ``client.chat.completions.create`` with a forced tool call
    (``tool_choice={"type":"function","function":{"name":tool_name}}``) so the
    plan comes back as ``message.tool_calls[0].function.arguments`` — the path
    the configured provider actually honors. If the forced-tool call raises
    (provider rejects tools / forced tool choice), retries as a plain
    completion and parses JSON out of ``content``. Any exception after both
    attempts is logged and swallowed — a broken judge must never surface
    (both memory passes are fail-open by contract).

    Returns the decoded plan dict, or ``None`` if neither path produced one.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    base: Dict[str, Any] = dict(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    tool_def = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": "Emit the structured plan you decided on. Use this tool; do not answer in prose.",
            "parameters": tool_schema,
        },
    }
    tool_choice = {"type": "function", "function": {"name": tool_name}}

    # Attempt 1: forced tool call — the path known to work on the configured
    # provider (returns finish_reason="tool_calls" with valid JSON arguments).
    try:
        response = client.chat.completions.create(
            tools=[tool_def], tool_choice=tool_choice, **base
        )
        parsed = parse_judge_response(response)
        if parsed is not None:
            return parsed
        logger.warning("[memory-judge] Forced-tool call returned no parsable plan; "
                       "falling back to plain completion (model=%s tool=%s)", model, tool_name)
    except Exception as exc:
        logger.warning("[memory-judge] Forced-tool call failed (%s); retrying as "
                       "plain completion (model=%s tool=%s)", exc, model, tool_name)

    # Attempt 2: plain completion — JSON parsed out of content. This is the
    # fallback for providers that reject forced tools, and also the path the
    # callers' _FakeClient(content=payload) unit tests exercise (the fake
    # client's create() happily accepts and ignores the tools/ tool_choice
    # kwargs on attempt 1, but returns content; parse_judge_response reads that
    # content here only if attempt 1 didn't already succeed — it will, since
    # the fake returns content on every call).
    try:
        response = client.chat.completions.create(**base)
        parsed = parse_judge_response(response)
        if parsed is not None:
            return parsed
    except Exception:
        logger.exception("[memory-judge] Plain completion fallback failed (model=%s tool=%s)",
                         model, tool_name)

    logger.warning("[memory-judge] No parsable plan from either attempt (model=%s tool=%s)",
                   model, tool_name)
    return None