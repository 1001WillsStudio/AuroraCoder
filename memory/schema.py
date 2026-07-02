"""
Memory schema — the typed-memory data model and its markdown+frontmatter
serialization.

Mirrors the design in ``docs/code-agent-memory-design.md`` §9-10: every
memory is a small markdown file with YAML-ish frontmatter (id, plane,
type, scope, description, confidence, provenance, ...) followed by the
memory content itself.

No third-party YAML dependency is introduced — the frontmatter is a
simple ``key: value`` block, which is all this schema needs.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# ── Taxonomy (design doc §9) ────────────────────────────────────────────────

MEMORY_PLANES = ("stance", "world")

MEMORY_TYPES = (
    # Stance plane
    "preference", "feedback", "communication", "autonomy",
    # World plane
    "project", "reference", "convention", "landmine",
    # Self-authored (Phase 3 add-ons — reflection/gap resolution)
    "lesson", "gap_resolution",
)

MEMORY_SCOPES = ("user", "project")

CONFIDENCE_LEVELS = ("high", "medium", "low")

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_memory_id() -> str:
    return f"mem_{uuid.uuid4().hex[:10]}"


@dataclass
class MemoryItem:
    """One typed memory entry.

    Field meanings follow docs/code-agent-memory-design.md §10 verbatim.
    """

    content: str
    description: str
    plane: str = "world"          # "stance" | "world"
    type: str = "project"         # see MEMORY_TYPES
    scope: str = "project"        # "user" | "project"
    id: str = field(default_factory=new_memory_id)
    confidence: str = "medium"    # "high" | "medium" | "low"
    provenance: str = "agent-stated"
    volatile: bool = False
    ttl_days: Optional[int] = None
    usage_count: int = 0
    last_used: Optional[str] = None
    created: str = field(default_factory=_now_iso)
    supersedes: Optional[str] = None

    def __post_init__(self):
        if self.plane not in MEMORY_PLANES:
            raise ValueError(f"invalid plane: {self.plane!r}")
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"invalid confidence: {self.confidence!r}")
        if self.scope not in MEMORY_SCOPES:
            raise ValueError(f"invalid scope: {self.scope!r}")

    # -- serialization ------------------------------------------------------

    def to_markdown(self) -> str:
        """Serialize to the ``---\\nkey: value\\n---\\ncontent`` file format."""
        meta = asdict(self)
        content = meta.pop("content")
        lines = ["---"]
        for key, value in meta.items():
            if value is None:
                lines.append(f"{key}: null")
            elif isinstance(value, bool):
                lines.append(f"{key}: {'true' if value else 'false'}")
            else:
                # Quote strings that could otherwise confuse the simple
                # parser (leading/trailing whitespace, colons, quotes).
                s = str(value).replace("\n", " ").strip()
                lines.append(f'{key}: "{s}"' if _needs_quoting(s) else f"{key}: {s}")
        lines.append("---")
        lines.append(content.strip())
        return "\n".join(lines) + "\n"

    @classmethod
    def from_markdown(cls, text: str) -> "MemoryItem":
        m = _FRONTMATTER_RE.match(text)
        if not m:
            raise ValueError("memory file missing frontmatter block")
        raw_meta, content = m.group(1), m.group(2)
        meta: Dict[str, Any] = {}
        for line in raw_meta.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            meta[key.strip()] = _coerce(value.strip())
        meta["content"] = content.strip()
        # Drop unknown keys defensively (forward-compat with older/newer files)
        valid_keys = {f for f in cls.__dataclass_fields__}
        meta = {k: v for k, v in meta.items() if k in valid_keys}
        return cls(**meta)

    def touch_usage(self) -> None:
        """Record a retrieval hit — feeds the decay/retention loop (§17)."""
        self.usage_count += 1
        self.last_used = _now_iso()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _needs_quoting(s: str) -> bool:
    return (
        s == ""
        or s[0] in "\"'"
        or ":" in s
        or s.lower() in ("true", "false", "null")
    )


def _coerce(value: str) -> Any:
    """Best-effort scalar coercion for the simple frontmatter parser."""
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    if value == "null":
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value
