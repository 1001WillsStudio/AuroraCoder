"""
Review gate for the in-session ``remember`` tool — design doc §11 "Active
(in-turn, high precision)".

Called synchronously from ``POST /api/memory/remember`` (gateway/routes.py)
*before* anything is written. Unlike everywhere else in this memory system,
this module is deliberately **fail-closed**: if the reviewer LLM call
errors out, the candidate is rejected rather than written unchecked. A
moderation gate that silently disables itself on error isn't a gate — and
per the user's own call, memory-write accuracy matters far more than
`remember`'s tool-call latency.

Reuses the same provider config as extraction (``get_memory_extraction_config``)
since this is also a structured-output-only, no-tool call.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI

from gateway.provider_registry import get_memory_extraction_config
from gateway.settings_store import get_other_settings
from gateway.memory.ops.prompts import REVIEW_SYSTEM_PROMPT, build_review_user_prompt

logger = logging.getLogger(__name__)

REVIEW_MAX_TOKENS = 512
SIMILAR_CANDIDATES_LIMIT = 5


def review_enabled() -> bool:
    mem = get_other_settings().get("memory", {})
    return bool(mem.get("remember_review_enabled", True))


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


def find_similar_existing(repo, plane: str, scope: str, description: str, limit: int = SIMILAR_CANDIDATES_LIMIT) -> List[Dict[str, Any]]:
    """Cheap keyword-overlap search over existing memories in the same
    plane/scope, to give the reviewer enough context to catch duplicates.
    Reuses the same tokenizer approach as the M2 consolidator — no
    embeddings, consistent with the rest of this MVP's retrieval.
    """
    from gateway.memory.ops.consolidator import _tokens

    cand_tokens = _tokens(description)
    if not cand_tokens:
        return []

    scored = []
    for row in repo.list(plane=plane, scope=scope):
        other_tokens = _tokens(row["description"])
        if not other_tokens:
            continue
        overlap = len(cand_tokens & other_tokens) / len(cand_tokens | other_tokens)
        if overlap > 0.15:
            scored.append((overlap, row))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [
        {"id": row["id"], "description": row["description"], "type": row["type"], "confidence": row["confidence"]}
        for _, row in scored[:limit]
    ]


def review_candidate(candidate: Dict[str, Any], similar_existing: List[Dict[str, Any]], is_update: bool = False) -> Dict[str, Any]:
    """Return a decision dict: {decision, reason, duplicate_of, adjusted_plane, adjusted_confidence}.

    Fail-closed: any error (missing provider, network failure, unparsable
    output) returns decision="reject" with a clear reason, never raises.
    """
    if not review_enabled():
        return {"decision": "approve", "reason": "review disabled in settings", "duplicate_of": None,
                "adjusted_plane": None, "adjusted_confidence": None}

    try:
        cfg = get_memory_extraction_config()
        if not cfg.get("api_key") or not cfg.get("base_url"):
            return {"decision": "reject", "reason": "no memory-review provider configured", "duplicate_of": None,
                    "adjusted_plane": None, "adjusted_confidence": None}

        client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])
        user_prompt = build_review_user_prompt(candidate, similar_existing, is_update)

        kwargs: Dict[str, Any] = dict(
            model=cfg["model"],
            messages=[
                {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=REVIEW_MAX_TOKENS,
            temperature=0,
        )
        try:
            response = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
        except Exception:
            response = client.chat.completions.create(**kwargs)

        raw = response.choices[0].message.content or ""
        parsed = _extract_json(raw)
        if not parsed or parsed.get("decision") not in ("approve", "reject"):
            logger.warning("[memory-review] Could not parse reviewer output, failing closed: %r", raw[:300])
            return {"decision": "reject", "reason": "reviewer returned unparsable output", "duplicate_of": None,
                    "adjusted_plane": None, "adjusted_confidence": None}

        return {
            "decision": parsed["decision"],
            "reason": parsed.get("reason", ""),
            "duplicate_of": parsed.get("duplicate_of"),
            "adjusted_plane": parsed.get("adjusted_plane"),
            "adjusted_confidence": parsed.get("adjusted_confidence"),
        }

    except Exception:
        logger.exception("[memory-review] Review call failed — failing closed (rejecting)")
        return {"decision": "reject", "reason": "memory review unavailable (internal error)", "duplicate_of": None,
                "adjusted_plane": None, "adjusted_confidence": None}
