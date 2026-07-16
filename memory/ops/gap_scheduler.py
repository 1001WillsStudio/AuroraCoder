"""
Gap Engine periodic scheduler — the automatic half of "when does Layer 2b
get triggered?"

Until now, ``dispatch_gap_investigation`` (memory/ops/dispatcher.py) had
exactly one entry point: ``POST /api/memory/gaps/{gap_id}/investigate``,
which nothing in this codebase calls on its own — a human/script/test has
to name a specific gap id. With no Gap Ledger browser UI built yet (see
docs/memory-module-structure.md §11), that meant Layer 2b was effectively
unreachable in normal operation. This module adds the missing automatic
path, alongside (not instead of) that manual one.

## Design: periodic sweep, priority-gated, not session-triggered

docs/code-agent-memory-design.md §13's resolution policy is explicit:
"cheap + locally answerable -> self-investigate (preferred); expensive /
subjective / blocking -> ask the user." A gap only reaches
``priority="high"`` here via recurrence escalation (see
``GapLedger.log_gap``'s duplicate-detection in ``memory/gap_store.py``) —
i.e. the same question came up independently more than once. That is
exactly the strong, code-computed signal (not a self-report) the "cheap and
worth resolving" half of that policy is describing, so this scheduler only
ever auto-fires on ``priority="high"`` gaps. Medium/low-priority gaps stay
manual-only, matching "expensive/subjective -> ask" — nothing here changes
that path.

Deliberately decoupled from session activity (not "check when a session
starts/ends") — a session boundary says nothing about whether now is a
good time to spend a whole container + LLM call investigating a gap that
may be completely unrelated to what that session was about. A plain
timer, independent of user activity, is simpler and doesn't couple this to
the streaming/session-lifecycle code at all.

## Why this needs no new backoff/cooldown bookkeeping

``dispatch_gap_investigation`` already calls ``ledger.defer(gap_id)`` on
EVERY failure path (spawn failure, worker never ready, no transcript, the
write pass rejecting the finding, or any unexpected exception) — see that
function's docstring. ``defer`` sets ``status="deferred"``, which is
permanently outside every ``status="open"`` query this scheduler makes.
So a gap that failed once simply never gets automatically retried again —
no explicit cooldown timer or attempt counter needed. (A human can still
manually re-investigate a deferred gap via the API; a fresh recurrence of
the same question also opens a brand-new gap row that can independently
earn its own way back to ``priority="high"``.)

## Crash recovery for stuck "investigating" gaps

The one gap state ``dispatch_gap_investigation`` can't defer out of is an
ungraceful process death (host crash, OOM-kill) between
``ledger.set_status(gap_id, "investigating")`` and its ``finally`` block —
every ordinary exception is already caught. Since dispatch runs
synchronously within one gateway process, a fresh process start means
nothing from a previous incarnation can still be "running" it — so
``recover_stale_investigating_gaps()`` resets any gap still at
``status="investigating"`` back to ``open`` once, at startup, rather than
using a time-based watchdog.

## Concurrency

``_running_gap_ids`` is a plain module-level ``set()``, safe without a
lock because it's only ever touched from asyncio callbacks on the gateway's
single event loop thread (same single-process assumption the rest of this
subsystem already makes — see ``memory/store.py``'s and
``memory/gap_store.py``'s singleton pattern). Each dispatch runs off-thread
via ``asyncio.to_thread`` (dispatch is a blocking call: subprocess +
long-poll HTTP), exactly like the manual route in ``gateway/routes.py``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Set

from memory.gap_store import get_gap_ledger
from memory.settings import (
    gap_auto_sweep_enabled,
    gap_sweep_batch_size,
    gap_sweep_interval_hours,
    gap_sweep_max_concurrent,
    heavy_ops_enabled,
)

logger = logging.getLogger(__name__)

_running_gap_ids: Set[str] = set()


def recover_stale_investigating_gaps() -> int:
    """Reset any gap stuck at ``status="investigating"`` back to ``open``.

    Only possible source: a previous process died mid-investigation
    without reaching ``dispatch_gap_investigation``'s ``finally`` block
    (see module docstring). Safe to call unconditionally at startup even
    if heavy ops was never enabled — there would be nothing to recover.
    """
    ledger = get_gap_ledger()
    stuck = ledger.list(status="investigating")
    for gap in stuck:
        ledger.set_status(gap["gap_id"], "open")
    if stuck:
        logger.warning(
            "[gap-scheduler] Recovered %d gap(s) stuck at status=investigating "
            "from a previous process — reset to open for re-eligibility.",
            len(stuck),
        )
    return len(stuck)


async def _dispatch_and_release(gap_id: str) -> None:
    from memory.ops.dispatcher import dispatch_gap_investigation

    try:
        result = await asyncio.to_thread(dispatch_gap_investigation, gap_id)
        logger.info("[gap-scheduler] Auto-dispatched gap %s -> %s", gap_id, result)
    except Exception:
        logger.exception("[gap-scheduler] Unexpected error auto-dispatching gap %s", gap_id)
    finally:
        _running_gap_ids.discard(gap_id)


async def sweep_once() -> Dict[str, Any]:
    """Check the Gap Ledger and dispatch eligible gaps, up to the
    configured batch size and concurrency cap. Never raises — a failed
    sweep tick should never take down the scheduler loop.

    Eligible = ``status="open"`` AND ``priority="high"`` (see module
    docstring). Returns a small summary dict, mostly useful for tests and
    logging.
    """
    if not gap_auto_sweep_enabled():
        return {"eligible": 0, "dispatched": 0, "skipped": 0}

    try:
        ledger = get_gap_ledger()
        eligible = [g for g in ledger.list(status="open") if g.get("priority") == "high"]
        eligible.sort(key=lambda g: g.get("opened_at", ""))

        available_slots = max(0, gap_sweep_max_concurrent() - len(_running_gap_ids))
        budget = min(gap_sweep_batch_size(), available_slots)

        dispatched = 0
        for gap in eligible:
            if dispatched >= budget:
                break
            gap_id = gap["gap_id"]
            if gap_id in _running_gap_ids:
                continue
            _running_gap_ids.add(gap_id)
            asyncio.create_task(_dispatch_and_release(gap_id))
            dispatched += 1

        skipped = len(eligible) - dispatched
        logger.info(
            "[gap-scheduler] Sweep: %d eligible (open, priority=high), dispatched %d, %d left for a later tick",
            len(eligible), dispatched, skipped,
        )
        return {"eligible": len(eligible), "dispatched": dispatched, "skipped": skipped}
    except Exception:
        logger.exception("[gap-scheduler] Sweep tick failed unexpectedly")
        return {"eligible": 0, "dispatched": 0, "skipped": 0}


async def run_periodic_gap_sweep() -> None:
    """Long-running task: recover once, then sweep on a fixed interval,
    forever. Intended to be started once via ``asyncio.create_task`` from
    the gateway's startup hook (``gateway/api.py``) — safe to start
    unconditionally even when heavy ops is off for this install, since
    each tick is a cheap no-op in that case (a settings read plus, at
    most, an empty ledger query).
    """
    recover_stale_investigating_gaps()
    while True:
        try:
            if heavy_ops_enabled():
                await sweep_once()
        except Exception:
            logger.exception("[gap-scheduler] Unexpected error in sweep loop")
        await asyncio.sleep(max(0.01, gap_sweep_interval_hours()) * 3600)
