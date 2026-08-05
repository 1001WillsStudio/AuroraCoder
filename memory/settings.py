"""
Central feature-flag reader for the memory subsystem.

Single source of truth for ``settings.other.memory.*`` so every consumer —
gateway routes, the session-end trigger in ``gateway/streaming.py``, the
extractor, the gap dispatcher, and the periodic gap scheduler
(``memory/ops/gap_scheduler.py``) — agrees on what "disabled" means.
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


def gap_auto_sweep_enabled() -> bool:
    """Layer 2b's periodic, unattended sweep (memory/ops/gap_scheduler.py).

    A separate opt-out from ``heavy_ops_enabled`` itself: turning heavy ops
    on is what lets investigation happen AT ALL (manually, via
    ``POST /api/memory/gaps/{id}/investigate``); this flag additionally
    gates whether the gateway spawns worker containers on its OWN schedule,
    unattended, without a human/UI asking for it each time. Defaults to
    True once heavy_ops is on — see memory/ops/gap_scheduler.py's module
    docstring for why that's judged safe (only ever touches
    recurrence-escalated ``priority="high"`` gaps, bounded concurrency).
    """
    return heavy_ops_enabled() and bool(_memory_settings().get("gap_auto_sweep_enabled", True))


def gap_sweep_interval_hours() -> float:
    """How often the periodic sweep wakes up to check the Gap Ledger."""
    return float(_memory_settings().get("gap_sweep_interval_hours", 24))


def gap_sweep_max_concurrent() -> int:
    """Max worker containers the periodic sweep will have running at once."""
    return max(1, int(_memory_settings().get("gap_sweep_max_concurrent", 1)))


def gap_sweep_batch_size() -> int:
    """Max NEW gaps the periodic sweep will dispatch in a single tick."""
    return max(1, int(_memory_settings().get("gap_sweep_batch_size", 1)))


def max_memories() -> int:
    """Hard ceiling on how many memories are kept.

    A conservative safety net rather than an expected limit — typical
    volumes stay far below it (50 is plenty for current usage). When the
    count exceeds the cap, low-value *world* memories are evicted to fit
    (see ``MemoryRepository.enforce_capacity``): least usage, oldest
    ``last_used``, least corroboration, oldest created get removed first.

    Stance memories are never removed automatically (explicit-overwrite /
    human edit only, per the design doc), so if stance alone exceeds the
    cap the surplus is left in place and merely logged.
    """
    return max(0, int(_memory_settings().get("max_memories", 50)))
