"""
Gap Ledger — design doc §8 ("Log gap in Gap Ledger") / §13 (Gap Engine).

A first-class persisted record of things the agent noticed it *didn't*
know and either guessed at or deferred, so unresolved knowledge gaps are
observable and convergent across sessions instead of being re-guessed
every time.

This module only implements the **light** half: logging, listing, and
resolving/deferring gaps (all synchronous, in-process, always on). The
**heavy** half — actually spending tool calls to self-investigate an
open gap — is Layer 2b and lives in ``ops/dispatcher.py``, gated behind
a disabled-by-default setting because it requires spinning up an
isolated worker container (see that module's docstring).

Deliberately a separate SQLite file (``gaps.sqlite``) from the memory
index, per the design doc's storage layout (§10) — gaps are a
work-queue, not retrieval metadata, and have a different lifecycle.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .store import DEFAULT_STORAGE_DIR

logger = logging.getLogger(__name__)

GAP_STATUSES = {"open", "investigating", "resolved", "deferred", "asked"}
GAP_STRATEGIES = {"self", "ask"}
GAP_PRIORITIES = {"low", "medium", "high"}
_PRIORITY_RANK = {"low": 0, "medium": 1, "high": 2}

# Two logged gaps on the same scope are treated as "the same recurring
# gap" (escalate priority instead of creating a duplicate row) once their
# questions overlap this much — see design doc §13 "repeated correction
# on the same axis (recurring gap = high priority)".
_DUPLICATE_SIMILARITY_THRESHOLD = 0.6

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set:
    return set(w for w in _WORD_RE.findall(text.lower()) if len(w) > 2)


def _similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_gap_id() -> str:
    import uuid
    return f"gap_{uuid.uuid4().hex[:10]}"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS gaps (
    gap_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    detected_from TEXT,
    strategy TEXT,
    resolved_memory_id TEXT,
    confidence TEXT,
    opened_at TEXT NOT NULL,
    resolved_at TEXT,
    reverify_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_gaps_status ON gaps(status);
"""


class GapLedger:
    """Thread-safe SQLite-backed gap ledger."""

    def __init__(self, storage_dir: Optional[Path] = None):
        self._dir = storage_dir or DEFAULT_STORAGE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._dir / "gaps.sqlite"
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------

    def log_gap(
        self,
        question: str,
        scope: str = "project",
        priority: str = "medium",
        detected_from: str = "agent",
        strategy: str = "ask",
    ) -> Dict[str, Any]:
        """Open a new gap, or escalate priority on a matching open one.

        Returns the gap row (dict) either way — callers shouldn't need to
        distinguish "created" from "escalated existing".
        """
        if priority not in GAP_PRIORITIES:
            priority = "medium"
        if strategy not in GAP_STRATEGIES:
            strategy = "ask"

        with self._lock, self._connect() as conn:
            open_rows = conn.execute(
                "SELECT * FROM gaps WHERE scope=? AND status IN ('open','investigating')",
                (scope,),
            ).fetchall()
            for row in open_rows:
                if _similarity(row["question"], question) >= _DUPLICATE_SIMILARITY_THRESHOLD:
                    ranks = ["low", "medium", "high"]
                    current_rank = _PRIORITY_RANK.get(row["priority"], 1)
                    incoming_rank = _PRIORITY_RANK.get(priority, 1)
                    # A recurrence of the same gap is itself a signal worth escalating by
                    # one level, on top of whatever the newly-reported priority says.
                    escalated_rank = min(current_rank + 1, len(ranks) - 1)
                    new_priority = ranks[max(current_rank, incoming_rank, escalated_rank)]
                    conn.execute("UPDATE gaps SET priority=? WHERE gap_id=?", (new_priority, row["gap_id"]))
                    logger.info("[gap] Recurring gap detected, escalated %s to priority=%s", row["gap_id"], new_priority)
                    updated = conn.execute("SELECT * FROM gaps WHERE gap_id=?", (row["gap_id"],)).fetchone()
                    return dict(updated)

            gap_id = _new_gap_id()
            now = _now_iso()
            conn.execute(
                """
                INSERT INTO gaps (gap_id, scope, question, status, priority, detected_from,
                                   strategy, resolved_memory_id, confidence, opened_at, resolved_at, reverify_at)
                VALUES (?, ?, ?, 'open', ?, ?, ?, NULL, NULL, ?, NULL, NULL)
                """,
                (gap_id, scope, question, priority, detected_from, strategy, now),
            )
            logger.info("[gap] Logged new gap %s (scope=%s, priority=%s): %s", gap_id, scope, priority, question)
            row = conn.execute("SELECT * FROM gaps WHERE gap_id=?", (gap_id,)).fetchone()
            return dict(row)

    def get(self, gap_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM gaps WHERE gap_id=?", (gap_id,)).fetchone()
        return dict(row) if row else None

    def list(self, status: Optional[str] = None, scope: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM gaps {where} ORDER BY opened_at DESC"
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, gap_id: str, status: str) -> bool:
        if status not in GAP_STATUSES:
            raise ValueError(f"invalid gap status: {status!r}")
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT gap_id FROM gaps WHERE gap_id=?", (gap_id,)).fetchone()
            if row is None:
                return False
            conn.execute("UPDATE gaps SET status=? WHERE gap_id=?", (status, gap_id))
        return True

    def resolve(self, gap_id: str, resolved_memory_id: str, confidence: str = "medium", reverify_at: Optional[str] = None) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT gap_id FROM gaps WHERE gap_id=?", (gap_id,)).fetchone()
            if row is None:
                return False
            conn.execute(
                """
                UPDATE gaps SET status='resolved', resolved_memory_id=?, confidence=?,
                                resolved_at=?, reverify_at=?
                WHERE gap_id=?
                """,
                (resolved_memory_id, confidence, _now_iso(), reverify_at, gap_id),
            )
        logger.info("[gap] Resolved %s -> memory %s", gap_id, resolved_memory_id)
        return True

    def defer(self, gap_id: str) -> bool:
        return self.set_status(gap_id, "deferred")


_ledger: Optional[GapLedger] = None
_ledger_lock = threading.Lock()


def get_gap_ledger() -> GapLedger:
    global _ledger
    if _ledger is None:
        with _ledger_lock:
            if _ledger is None:
                _ledger = GapLedger()
    return _ledger
