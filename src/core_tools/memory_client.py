"""
Memory client — thin HTTP client to the gateway's ``/api/memory/*`` API.

The backend never touches the memory store's files/SQLite directly.
Gateway is the sole owner (same precedent as ``gateway/conversation_store.py``
for conversations) — this module is the only bridge, mirroring how
``core_tools/subagent.py`` is the backend's HTTP bridge to the gateway's
conversation pipeline.

Note there is no ``remember()`` here: writing memory is no longer a
runtime operation at all. The agent's ``remember`` tool
(``memory_tools.remember_tool``) is purely local — it leaves a marker in
the transcript and returns immediately, with zero calls to this module.
The gateway's unified end-of-session pass (``gateway/memory/ops/
extractor.py``) parses those markers back out of the transcript and
judges them with full conversation context, alongside anything it
discovers on its own. See that module's docstring for why.

Every function here fails open: on any network/parse error it returns an
inert default (empty stance, empty recall results, a clear error string
for gap writes) rather than raising — a memory outage must never break
the agent's turn loop (design doc §18 "fail-open").
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8081")

_TIMEOUT = 5.0  # seconds — memory calls must never be allowed to stall a turn


def get_stance(scope: Optional[str] = None) -> str:
    """Fetch the Stance block once per session (see main_flow.py — only
    called on the first turn of a conversation, before the system message
    is inserted). Returns "" on any failure."""
    try:
        resp = requests.get(
            f"{GATEWAY_URL}/api/memory/stance",
            params={"scope": scope} if scope else {},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("stance", "")
    except Exception as e:
        logger.warning("[memory_client] get_stance failed: %s", e)
        return ""


def recall(query: str, plane: str = "world", scope: Optional[str] = None, k: int = 5) -> List[Dict[str, Any]]:
    """Query the World Model. Returns [] on any failure (fail-open)."""
    try:
        resp = requests.get(
            f"{GATEWAY_URL}/api/memory/recall",
            params={"query": query, "plane": plane, "scope": scope or "", "k": k},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        logger.warning("[memory_client] recall failed: %s", e)
        return []


def log_gap(
    question: str,
    scope: str = "project",
    priority: str = "medium",
    strategy: str = "ask",
) -> Dict[str, Any]:
    """Flag a knowledge gap for later resolution (Gap Ledger, design doc §13).

    Returns {"ok": bool, "gap"/"error": ...}.
    """
    payload = {"question": question, "scope": scope, "priority": priority, "strategy": strategy}
    try:
        resp = requests.post(f"{GATEWAY_URL}/api/memory/gaps", json=payload, timeout=_TIMEOUT)
        if resp.status_code >= 400:
            return {"ok": False, "error": f"gateway returned {resp.status_code}: {resp.text[:300]}"}
        return resp.json()
    except Exception as e:
        logger.warning("[memory_client] log_gap failed: %s", e)
        return {"ok": False, "error": str(e)}
