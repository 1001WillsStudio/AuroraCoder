"""
``remember`` / ``recall`` tool implementations — the agent-facing surface
of the memory system's Layer 1 (light runtime).

Both are thin wrappers around ``memory_client`` — all real work (storage,
ranking, redaction) happens in the gateway process; see
``gateway/memory/``.  Kept deliberately dumb here so the backend never
needs to touch the on-disk store.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from . import memory_client


def remember_tool(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    result = memory_client.remember(
        content=arguments["content"],
        description=arguments["description"],
        plane=arguments.get("plane", "world"),
        type=arguments.get("type", "project"),
        scope=arguments.get("scope", "project"),
        confidence=arguments.get("confidence", "medium"),
        provenance=arguments.get("provenance", "agent-stated"),
        volatile=arguments.get("volatile", False),
        ttl_days=arguments.get("ttl_days"),
        supersedes=arguments.get("supersedes"),
        memory_id=arguments.get("memory_id"),
    )
    if result.get("ok"):
        verb = "Updated" if arguments.get("memory_id") else "Remembered"
        return f"{verb} (id={result['id']}): {arguments['description']}", arguments
    return f"Failed to write memory: {result.get('error') or result.get('reason', 'unknown error')}", arguments


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
