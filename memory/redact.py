"""
Secret redaction — applied at write time, before a memory ever touches
disk (design doc §18: "Redact secrets at write time").

Deliberately conservative regex set covering the common high-confidence
secret shapes (API keys, bearer tokens, private key blocks, connection
strings with embedded credentials). This is a safety net, not a
substitute for not writing secrets into memory in the first place — the
``remember`` tool description also instructs the agent never to store
credentials.
"""

from __future__ import annotations

import re
from typing import Tuple

REDACTED = "[REDACTED_SECRET]"

_PATTERNS = [
    # Generic "key/token/secret/password: <value>" assignments
    re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd|access[_-]?key)\b"
        r"\s*[:=]\s*['\"]?([A-Za-z0-9_\-/+=.]{8,})['\"]?"
    ),
    # OpenAI-style keys
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    # GitHub tokens
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    # AWS access key IDs
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # Slack tokens
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    # Bearer auth headers
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.=]{16,}\b"),
    # Credentials embedded in URLs: scheme://user:pass@host
    re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)([^\s/:@]+):([^\s/@]+)@"),
    # PEM private key blocks
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
]


def redact(text: str) -> Tuple[str, int]:
    """Return ``(redacted_text, num_redactions)``.

    Applied to both ``content`` and ``description`` before a memory is
    persisted. Safe to call on already-clean text (no-op).
    """
    if not text:
        return text, 0

    count = 0
    out = text
    for pattern in _PATTERNS:
        out, n = pattern.subn(REDACTED, out)
        count += n
    return out, count
