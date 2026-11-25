"""Record provider failure metadata without saving prompt or response bodies."""

import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone

from .config import DATA_DIR

logger = logging.getLogger(__name__)
_ROLES = {"system", "developer", "user", "assistant", "tool", "function"}
_FIELDS = {"content", "thinking", "reasoning_content", "tool_calls", "tool_call_id", "name", "ts"}
_PARAM = re.compile(
    r"(?:model|max_tokens|max_completion_tokens|tools|tool_choice|stream_options|messages"
    r"(?:(?:\[\d{1,6}\]|\.\d{1,6})\.(?:role|content|thinking|reasoning_content|"
    r"tool_calls|tool_call_id|name|ts))?)\Z"
)


def record_provider_failure(exc: Exception, provider: str, model: str, conversation_id: str | None,
                            request: dict) -> None:
    """Keep enough request shape to diagnose provider compatibility and availability."""
    messages = [msg for msg in request.get("messages") or [] if isinstance(msg, dict)]
    roles = Counter(
        role if isinstance(role, str) and role in _ROLES else "other"
        for role in (msg.get("role") for msg in messages)
    )
    fields = Counter(field for msg in messages for field in _FIELDS.intersection(msg))
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        body = body.get("error", body)
    param = body.get("param") if isinstance(body, dict) else None
    safe_param = param if isinstance(param, str) and _PARAM.fullmatch(param) else None
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "conversation_id": conversation_id,
        "provider": provider,
        "model": model,
        "error": type(exc).__name__,
        "status": getattr(exc, "status_code", None),
        "param": safe_param,
        "request": {"roles": dict(roles), "fields": dict(sorted(fields.items())),
                    "tool_count": len(request.get("tools") or [])},
    }
    try:
        path = DATA_DIR / "diagnostics" / "llm_failures.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("Could not write provider failure diagnostics")
