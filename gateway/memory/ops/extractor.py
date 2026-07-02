"""
Passive extraction (Layer 2a) — design doc §11 "Passive (async,
post-session)" / Codex Phase 1.

Runs entirely inside the gateway process: a single structured-output
completion call over the finished transcript, no tool access, no
sandbox. Safe by construction — this is exactly why it doesn't need the
isolated worker container that Layer 2b (Gap Engine) needs.

Triggered from ``gateway/streaming.py`` when a top-level user_chat
conversation reaches a terminal status (see ``schedule_extraction``).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI

from gateway.provider_registry import get_memory_extraction_config
from gateway.settings_store import get_other_settings
from gateway.memory.schema import MemoryItem, MEMORY_PLANES, MEMORY_TYPES
from gateway.memory.store import get_repository
from gateway.memory.ops.prompts import EXTRACTION_SYSTEM_PROMPT, build_extraction_user_prompt

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 20_000
MIN_MESSAGES_TO_BOTHER = 4  # skip trivial 1-2 turn conversations entirely
EXTRACTION_MAX_TOKENS = 2048


def extraction_enabled() -> bool:
    mem = get_other_settings().get("memory", {})
    return bool(mem.get("passive_enabled", True))


def _transcript_to_text(messages: List[Dict[str, Any]], max_chars: int = MAX_TRANSCRIPT_CHARS) -> str:
    """Render a compact, role-tagged transcript for the extraction prompt.

    Tool call arguments/results are summarized rather than included in
    full — extraction only needs the narrative (what was asked, what was
    decided, what corrections happened), not raw file contents.
    """
    lines: List[str] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            continue
        if role == "user":
            content = (msg.get("content") or "").strip()
            if content:
                lines.append(f"USER: {content}")
        elif role == "assistant":
            content = (msg.get("content") or "").strip()
            if content:
                lines.append(f"ASSISTANT: {content}")
            for tc in msg.get("tool_calls", []) or []:
                fn = tc.get("function", {})
                lines.append(f"  [called {fn.get('name', '?')}]")
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


def run_extraction(conversation_id: str, messages: List[Dict[str, Any]]) -> List[str]:
    """Run the extraction pass for one finished conversation.

    Returns the list of newly-written memory ids (empty list is the
    expected common case — the no-op gate is "allowed and preferred").
    Never raises: any failure is logged and treated as a no-op, since a
    broken extraction pass must never surface as a user-visible error for
    a conversation that already completed successfully.
    """
    if not extraction_enabled():
        return []
    if len(messages) < MIN_MESSAGES_TO_BOTHER:
        return []

    try:
        cfg = get_memory_extraction_config()
        if not cfg.get("api_key") or not cfg.get("base_url"):
            logger.info("[memory-extract] No provider configured — skipping.")
            return []

        client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])
        transcript = _transcript_to_text(messages)

        kwargs: Dict[str, Any] = dict(
            model=cfg["model"],
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": build_extraction_user_prompt(transcript)},
            ],
            max_tokens=EXTRACTION_MAX_TOKENS,
            temperature=0,
        )
        try:
            response = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
        except Exception:
            # Provider may not support response_format — retry without it.
            response = client.chat.completions.create(**kwargs)

        raw = response.choices[0].message.content or ""
        parsed = _extract_json(raw)
        if not parsed:
            logger.warning("[memory-extract] [%s] Could not parse model output as JSON", conversation_id[:8])
            return []

        candidates = parsed.get("memories", [])
        if not candidates:
            logger.info("[memory-extract] [%s] No-op (0 candidates) — expected common case", conversation_id[:8])
            return []

        repo = get_repository()
        written: List[str] = []
        for cand in candidates:
            try:
                if cand.get("plane") not in MEMORY_PLANES or cand.get("type") not in MEMORY_TYPES:
                    continue
                item = MemoryItem(
                    content=cand["content"],
                    description=cand["description"],
                    plane=cand["plane"],
                    type=cand["type"],
                    scope=cand.get("scope", "project"),
                    confidence=cand.get("confidence", "low"),
                    provenance=f"passive-extraction from conversation {conversation_id[:8]}",
                )
                repo.upsert(item)
                written.append(item.id)
            except (KeyError, ValueError) as e:
                logger.warning("[memory-extract] Skipped malformed candidate: %s", e)

        logger.info("[memory-extract] [%s] Wrote %d memor(y/ies)", conversation_id[:8], len(written))
        return written

    except Exception:
        logger.exception("[memory-extract] [%s] Extraction failed — treating as no-op", conversation_id[:8])
        return []
