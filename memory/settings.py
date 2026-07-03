"""
Central feature-flag reader for the memory subsystem.

Single source of truth for ``settings.other.memory.*`` so every consumer —
gateway routes, the session-end trigger in ``gateway/streaming.py``, the
extractor, and the gap dispatcher — agrees on what "disabled" means.
Reads fresh on every call rather than caching (settings can change at any
time via the Settings panel + ``POST /api/reload``), same pattern as
``gateway/settings_store.get_other_settings()`` elsewhere.

``memory_enabled()`` is the master switch. Every other flag here is
``memory_enabled() AND <its own sub-flag>`` — flipping the master off
turns off passive extraction and heavy ops too, without needing to
separately remember to flip every sub-flag. When the master is off, the
agent is meant to behave exactly as it would with no memory module at
all: see ``docs/memory-implementation-summary.md`` for the full list of
things that gate on this (tool exposure, stance injection, extraction,
gap logging, gap investigation).

Defaults to **disabled** — this is still an experimental subsystem (the
Gap Engine's investigation protocol in particular is unfinished
scaffolding), so new/existing installs opt in rather than getting it
silently turned on.
"""

from __future__ import annotations

from gateway.settings_store import get_other_settings


def _memory_settings() -> dict:
    return get_other_settings().get("memory", {})


def memory_enabled() -> bool:
    """Master switch for the whole memory subsystem. Defaults to ``False``."""
    return bool(_memory_settings().get("enabled", False))


def passive_extraction_enabled() -> bool:
    """Layer 2a — post-session LLM extraction/consolidation pass."""
    return memory_enabled() and bool(_memory_settings().get("passive_enabled", True))


def heavy_ops_enabled() -> bool:
    """Layer 2b — spawn an isolated worker container to investigate a gap."""
    return memory_enabled() and bool(_memory_settings().get("heavy_ops_enabled", False))
