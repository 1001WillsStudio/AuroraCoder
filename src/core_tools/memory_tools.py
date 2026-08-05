"""
``remember`` / ``recall`` / ``log_gap`` / ``forget`` / ``report_findings``
tool implementations — the agent-facing surface of the memory system.

``recall``, ``forget``, and ``log_gap`` are thin wrappers around
``memory_client`` — all real work (storage, ranking, redaction) happens in
the gateway process; see ``memory/``. ``remember`` and ``report_findings``
are pure local no-ops instead (see their docstrings) — both just leave a
marker in the transcript for ``memory/ops/extractor.py`` to parse and
judge after the session ends, rather than doing any I/O at call time.
Kept deliberately dumb here so the backend never needs to touch the
on-disk store.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from . import memory_client


def remember_tool(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Purely local — does no I/O at call time.

    Memory is no longer written synchronously. This call just leaves a
    marker in the conversation transcript; the gateway's unified
    end-of-session pass (memory/ops/extractor.py) parses it back
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

    # id is included so a follow-up `forget` call (if the user says one of
    # these is wrong) can target the exact memory instead of guessing.
    lines = [f'{len(results)} memor{"y" if len(results) == 1 else "ies"} found for "{query}":', ""]
    for r in results:
        lines.append(f"- id={r['id']} [{r['type']}] {r['description']} (confidence={r['confidence']})")
        lines.append(f"  {r['content']}")
    return "\n".join(lines), arguments


def forget_tool(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Immediately, permanently delete one memory by id.

    Unlike `remember`, this does real I/O at call time — an explicit
    "that's wrong / forget it" from the user is a direct instruction, not
    a claim that needs post-hoc judgment. Always `recall` first to get a
    real id; never guess one.
    """
    memory_id = arguments["memory_id"]
    result = memory_client.forget(memory_id)
    if result.get("ok"):
        return f"Forgot memory {memory_id}.", arguments
    return f"Could not forget memory {memory_id}: {result.get('error') or result.get('reason', 'not found')}", arguments


def report_findings_tool(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Purely local — does no I/O at call time, exactly like `remember`.

    Only meaningful inside an isolated gap-investigation worker session
    (see memory/ops/dispatcher.py): this call just leaves a marker in
    THAT session's transcript. The dispatcher, running outside the
    worker, reads the transcript back over HTTP once the session ends
    and hands it to memory/ops/extractor.py's
    ``run_gap_investigation_extraction`` — which parses this call exactly
    like a `remember` nomination and judges it through the same
    end-of-session write pass. Nothing is written from inside the worker
    itself.
    """
    if arguments.get("resolved"):
        description = arguments.get("description", "(no description given)")
        return (
            f'Findings recorded: "{description}". This will be judged and possibly saved to '
            f"long-term memory. Ending the session now."
        ), arguments
    notes = arguments.get("notes", "(no notes given)")
    return f"Recorded: could not resolve this gap — {notes}. Ending the session now.", arguments


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


def emit_memory_plan_tool(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Purely local — does no I/O at call time, exactly like ``remember`` and
    ``report_findings``.

    Only meaningful inside a memory-maintenance worker session (see
    ``memory/ops/dispatcher.py::dispatch_memory_maintenance``). This call just
    leaves the extraction plan as a marker in THAT session's transcript. The
    dispatcher, running in the main container, reads the transcript back over
    HTTP once the session ends, parses this tool call's arguments, and applies
    the plan (``memory/ops/extractor.py::apply_extraction_plan``). Nothing is
    written from inside the worker itself — same isolation contract as the gap
    investigation path.
    """
    n = len(arguments.get("memories", []) or [])
    return (
        f"Memory-extraction plan recorded ({n} candidate(s)). It will be applied by the "
        f"dispatcher once this session ends. Ending the maintenance task now."
    ), arguments


def emit_consolidation_plan_tool(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Purely local — consolidation twin of ``emit_memory_plan_tool``.

    Leaves the merge/delete plan as a marker in the maintenance worker's
    transcript; the dispatcher parses it back out and applies it via
    ``memory/ops/consolidator.py::_apply_plan`` on the main side.
    """
    merges = arguments.get("merges", []) or []
    deletes = arguments.get("deletes", []) or []
    return (
        f"Consolidation plan recorded ({len(merges)} merge(s), {len(deletes)} delete(s)). It "
        f"will be applied by the dispatcher once this session ends. Ending the maintenance task now."
    ), arguments
