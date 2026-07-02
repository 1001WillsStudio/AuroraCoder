"""
Memory Repository — file-backed storage + a SQLite index for ranking.

Follows the exact conventions ``gateway/conversation_store.py`` already
established for this codebase: atomic writes (temp file + rename),
a ``threading.Lock`` around all mutations, and a data directory resolved
the same way as every other persistent subsystem here.

Storage layout::

    {DATA_DIR}/memory/
        index.sqlite            # ranking / retrieval metadata (memories table)
        stance/{id}.md          # user-global "attitude" memories
        world/{id}.md           # project-scoped "knowledge gap" memories
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schema import MemoryItem
from .redact import redact

logger = logging.getLogger(__name__)


def _default_storage_dir() -> Path:
    if os.environ.get("AURORACODER_DOCKER", "0") == "1":
        return Path("/app/data/memory")
    return Path(os.environ.get(
        "AURORACODER_DATA_DIR",
        os.path.expanduser("~/.auroracoder/data"),
    )) / "memory"


DEFAULT_STORAGE_DIR = _default_storage_dir()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    plane TEXT NOT NULL,
    type TEXT NOT NULL,
    scope TEXT NOT NULL,
    description TEXT NOT NULL,
    confidence TEXT NOT NULL,
    provenance TEXT,
    volatile INTEGER NOT NULL DEFAULT 0,
    ttl_days INTEGER,
    usage_count INTEGER NOT NULL DEFAULT 0,
    last_used TEXT,
    created TEXT NOT NULL,
    supersedes TEXT,
    file_path TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_plane ON memories(plane);
CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope);
"""


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class MemoryRepository:
    """Thread-safe, file-backed memory store with a SQLite ranking index."""

    def __init__(self, storage_dir: Optional[Path] = None):
        self._dir = storage_dir or DEFAULT_STORAGE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / "stance").mkdir(exist_ok=True)
        (self._dir / "world").mkdir(exist_ok=True)
        self._lock = threading.Lock()
        self._db_path = self._dir / "index.sqlite"
        self._init_db()

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _file_path(self, item: MemoryItem) -> Path:
        return self._dir / item.plane / f"{item.id}.md"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upsert(self, item: MemoryItem) -> MemoryItem:
        """Create or overwrite a memory. Applies secret redaction first.

        "Update an existing memory before writing a new one" (design doc
        §7.1 item 2 / Claude Code convention) is the caller's
        responsibility — pass the existing ``id`` via ``supersedes`` or
        reuse it directly to dedupe.
        """
        item.content, n1 = redact(item.content)
        item.description, n2 = redact(item.description)
        if n1 or n2:
            logger.warning("[memory] Redacted %d secret(s) from memory %s", n1 + n2, item.id)

        path = self._file_path(item)
        rel_path = str(path.relative_to(self._dir))

        with self._lock:
            _atomic_write_text(path, item.to_markdown())
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO memories
                        (id, plane, type, scope, description, confidence, provenance,
                         volatile, ttl_days, usage_count, last_used, created, supersedes, file_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        plane=excluded.plane, type=excluded.type, scope=excluded.scope,
                        description=excluded.description, confidence=excluded.confidence,
                        provenance=excluded.provenance, volatile=excluded.volatile,
                        ttl_days=excluded.ttl_days, usage_count=excluded.usage_count,
                        last_used=excluded.last_used, created=excluded.created,
                        supersedes=excluded.supersedes, file_path=excluded.file_path
                    """,
                    (
                        item.id, item.plane, item.type, item.scope, item.description,
                        item.confidence, item.provenance, int(item.volatile), item.ttl_days,
                        item.usage_count, item.last_used, item.created, item.supersedes,
                        rel_path,
                    ),
                )
        logger.info("[memory] Upserted %s (%s/%s, scope=%s)", item.id, item.plane, item.type, item.scope)
        return item

    def get(self, memory_id: str) -> Optional[MemoryItem]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT file_path FROM memories WHERE id=?", (memory_id,)).fetchone()
        if row is None:
            return None
        return self._read_file(Path(row["file_path"]))

    def delete(self, memory_id: str) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT file_path FROM memories WHERE id=?", (memory_id,)).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        try:
            (self._dir / row["file_path"]).unlink(missing_ok=True)
        except OSError:
            pass
        return True

    def list(
        self,
        plane: Optional[str] = None,
        scope: Optional[str] = None,
        type: Optional[str] = None,
        order_by: str = "created",
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return index rows (metadata only, no file content) matching filters."""
        clauses, params = [], []
        if plane:
            clauses.append("plane = ?")
            params.append(plane)
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        if type:
            clauses.append("type = ?")
            params.append(type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order_col = order_by if order_by in ("created", "last_used", "usage_count") else "created"
        sql = f"SELECT * FROM memories {where} ORDER BY {order_col} DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def bump_usage(self, memory_ids: List[str]) -> None:
        """Record a retrieval hit for each id — feeds the decay/retention loop (§17)."""
        if not memory_ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.executemany(
                "UPDATE memories SET usage_count = usage_count + 1, last_used = ? WHERE id = ?",
                [(now, mid) for mid in memory_ids],
            )

    def _read_file(self, rel_path: Path) -> Optional[MemoryItem]:
        full = self._dir / rel_path
        try:
            text = full.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            return MemoryItem.from_markdown(text)
        except ValueError:
            logger.error("[memory] Corrupt memory file: %s", full)
            return None

    def all_items(self, plane: Optional[str] = None, scope: Optional[str] = None) -> List[MemoryItem]:
        """Return full MemoryItem objects (reads file content) for candidate ranking."""
        rows = self.list(plane=plane, scope=scope)
        items = []
        for row in rows:
            item = self._read_file(Path(row["file_path"]))
            if item is not None:
                items.append(item)
        return items


# ── Module-level singleton (mirrors gateway/conversation_store.py) ─────────

_repository: Optional[MemoryRepository] = None
_repository_lock = threading.Lock()


def get_repository() -> MemoryRepository:
    global _repository
    if _repository is None:
        with _repository_lock:
            if _repository is None:
                _repository = MemoryRepository()
    return _repository
