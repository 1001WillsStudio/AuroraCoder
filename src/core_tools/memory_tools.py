"""
``remember`` / ``recall`` / ``log_gap`` tool implementations — the
agent-facing surface of the memory system's Layer 1 (light runtime).

Both are thin wrappers around ``memory_client`` — all real work (storage,
ranking, redaction) happens in the gateway process; see
``gateway/memory/``.  Kept deliberately dumb here so the backend never
needs to touch the on-disk store.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from . import memory_client


def remember_tool(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Purely local — does no I/O at call time.

    Memory is no longer written synchronously. This call just leaves a
    marker in the conversation transcript; the gateway's unified
    end-of-session pass (gateway/memory/ops/extractor.py) parses it back
    out and judges it with full transcript context, alongside anything
    it discovers on its own. See that module's docstring for why: a
    synchronous mid-session review could only ever judge structural
    plausibility, never verify the claim was actually grounded in what
    happened, because it never saw the conversation.

    This means the memory is NOT immediately visible to `recall` in the
    same session, and it is not guaranteed to be kept — the same no-op
    bias applies to nominated candidates as to anything else.
    """
    description = arguments.get("description", "this")
    return (
        f'Noted — "{description}" will be judged and possibly saved to long-term memory at the '
        f"end of this session (not immediately available via `recall` in this session)."
    ), arguments


def recall_tool(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    query = arguments["query"]
    plane = arguments.get("plane", "world")
    k = arguments.get("k", 5)
    results = memory_client.recall(query=query, plane=plane, k=k)

    if not results:
        return f'No memories found for "{query}".', arguments

    lines = [f'{len(results)} memor{"y" if len(results) == 1 else "ies"} found for "{query}":', ""]
    for r in results:
        lines.append(f"- [{r['type']}] {r['description']} (confidence={r['confidence']})")
        lines.append(f"  {r['content']}")
    return "\n".join(lines), arguments


def log_gap_tool(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    result = memory_client.log_gap(
        question=arguments["question"],
        scope=arguments.get("scope", "project"),
        priority=arguments.get("priority", "medium"),
        strategy=arguments.get("strategy", "ask"),
    )
    if result.get("ok"):
        gap = result.get("gap", {})
        return f"Logged gap (id={gap.get('gap_id')}, priority={gap.get('priority')}): {arguments['question']}", arguments
    return f"Failed to log gap: {result.get('error') or result.get('reason', 'unknown error')}", arguments
