"""
Conversation Store — JSON file-based storage for conversation history.

Stores conversation metadata in an index file and full message lists in
individual JSON files.  Uses atomic writes (temp file + rename) to prevent
corruption on crash.

Thread-safe: all public methods acquire self._lock before mutating state.
"""

import json
import os
import uuid
import threading
import tempfile
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from gateway.attached_files import (
    ATTACHED_FILES_START,
    extract_attached_files,
    strip_attached_files,
)
from gateway.task_instruction_display import (
    TASK_INSTRUCTION_START,
    sanitize_frontend_messages,
    strip_task_instruction,
    user_message_for_frontend,
)

logger = logging.getLogger(__name__)


def _default_storage_dir() -> Path:
    if os.environ.get("AURORACODER_DOCKER", "0") == "1":
        return Path("/app/data/conversations")
    return Path(os.environ.get(
        "AURORACODER_DATA_DIR",
        os.path.expanduser("~/.auroracoder/data"),
    )) / "conversations"

DEFAULT_STORAGE_DIR = _default_storage_dir()

TERMINAL_STATUSES = frozenset({
    "completed", "error", "max_iterations_reached", "interrupted",
})

TITLE_MAX_LENGTH = 100


def messages_for_retry(
    messages: Optional[List[Dict]],
    user_text: str = "",
) -> List[Dict]:
    """Keep the existing transcript for retry; seed the user line only if empty.

    Incomplete tool rounds are made sendable by ``_fix_orphan_tool_calls``
    on the chat path — this helper does not re-trim them.
    """
    msgs = list(messages or [])
    if msgs:
        return msgs
    if user_text:
        return [{"role": "user", "content": user_text}]
    return []


def ensure_error_frontend_message(
    messages: Optional[List[Dict]],
    error: Optional[Dict] = None,
) -> List[Dict]:
    """Append a retryable error bubble unless the transcript already ends with one."""
    msgs = list(messages or [])
    if msgs and msgs[-1].get("isError"):
        return msgs
    message = ""
    err_type = ""
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("error") or "")
        err_type = str(error.get("type") or "")
    text = message or "The provider failed before a reply was produced."
    if not text.startswith("Error:"):
        text = f"Error: {text}"
    lowered = f"{text} {err_type}".lower()
    msgs.append({
        "role": "assistant",
        "content": text,
        "isError": True,
        "isTimeout": "timeout" in lowered or "504" in lowered or err_type == "TimeoutError",
        "canRetry": True,
    })
    return msgs


def _extract_title(messages: List[Dict]) -> str:
    """Extract a title from the first user message.

    Strips any frontend-injected task instruction block (bracketed by
    ``[TASK INSTRUCTION]`` / ``[/TASK INSTRUCTION]`` markers) so the
    title shows the actual user message, not the instruction prefix.

    Falls back to the last paragraph after a double newline only when
    no markers are present (legacy compatibility).
    """
    for msg in messages:
        if msg.get("role") != "user":
            continue
        files_field = msg.get("attachedFiles") if isinstance(msg.get("attachedFiles"), list) else []
        raw_content = msg.get("content")
        if not raw_content and not files_field:
            continue
        content = raw_content.strip() if isinstance(raw_content, str) else ""
        original = content

        # 1) Marked task instruction (new, reliable)
        if TASK_INSTRUCTION_START in original:
            content = strip_task_instruction(content)

        # 1b) Marked attached-files block (per-turn workspace chips)
        if ATTACHED_FILES_START in original:
            content = strip_attached_files(content)

        # 2) Legacy fallback: frontend used plain \n\n separator
        elif TASK_INSTRUCTION_START not in original and "\n\n" in original:
            content = content.rsplit("\n\n", 1)[-1]

        title = content.replace("\n", " ").strip()
        if not title:
            files = files_field or extract_attached_files(original) or []
            title = files[0] if files else "Untitled"
        if len(title) > TITLE_MAX_LENGTH:
            title = title[:TITLE_MAX_LENGTH] + "..."
        return title or "Untitled"
    return "Untitled"


def _atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON atomically: write to temp file then rename over target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".tmp_",
        suffix=".json",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            # Flush Python buffers AND force the OS to persist the data blocks
            # to disk before the rename.  Without this, a crash can leave the
            # renamed file present but its contents unwritten (all-zero bytes)
            # because the metadata rename outlived the unflushed data.
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class ConversationStore:
    """
    Thread-safe, file-backed conversation store.

    Storage layout::

        {storage_dir}/
            index.json                      # metadata for all conversations
            {conversation_id}.json          # full message list per conversation
    """

    def __init__(self, storage_dir: Optional[Path] = None):
        self._dir = storage_dir or DEFAULT_STORAGE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._index: Dict[str, Dict] = {}
        self._load_index()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _index_path(self) -> Path:
        return self._dir / "index.json"

    def _messages_path(self, conversation_id: str) -> Path:
        return self._dir / f"{conversation_id}.json"

    def _frontend_messages_path(self, conversation_id: str) -> Path:
        return self._dir / f"{conversation_id}.frontend.json"

    def _load_index(self) -> None:
        path = self._index_path()
        if not path.exists():
            self._index = {}
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError(f"index is a {type(loaded).__name__}, expected object")
            self._index = loaded
            return
        except (json.JSONDecodeError, OSError, ValueError) as e:
            # A crash (e.g. unexpected power loss) can leave index.json filled
            # with NUL bytes or truncated, which fails to parse.  Rather than
            # silently dropping every conversation, move the bad file aside and
            # rebuild the index from the per-conversation message files that
            # survived on disk.
            logger.warning(
                f"Corrupt conversation index ({e}); rebuilding from conversation files"
            )

        self._backup_corrupt_index(path)
        rebuilt = self._rebuild_index_from_files()
        self._index = rebuilt
        if rebuilt:
            try:
                self._save_index()
                logger.warning(
                    f"Rebuilt conversation index with {len(rebuilt)} conversation(s)"
                )
            except OSError as e:
                logger.error(f"Failed to persist rebuilt index: {e}")
        else:
            logger.warning("No conversation files found to rebuild index; starting fresh")

    def _backup_corrupt_index(self, path: Path) -> None:
        """Move a corrupt index aside so it can be inspected later."""
        try:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = path.with_name(f"index.corrupt-{ts}.json")
            os.replace(str(path), str(backup))
            logger.warning(f"Backed up corrupt index to {backup.name}")
        except OSError as e:
            logger.error(f"Could not back up corrupt index: {e}")

    def _rebuild_index_from_files(self) -> Dict[str, Dict]:
        """Reconstruct index metadata by scanning surviving message files.

        Type and parent/child relationships are not stored in the message
        files, so recovered conversations default to ``type="user_chat"`` with
        ``status="completed"`` and are flagged with ``recovered=True``.
        """
        index: Dict[str, Dict] = {}
        try:
            entries = list(self._dir.iterdir())
        except OSError as e:
            logger.error(f"Cannot scan storage dir for rebuild: {e}")
            return index

        cids = set()
        for p in entries:
            name = p.name
            if not name.endswith(".json"):
                continue
            if (
                name == "index.json"
                or name.startswith(".tmp_")
                or name.startswith("index.corrupt-")
            ):
                continue
            if name.endswith(".frontend.json"):
                cids.add(name[: -len(".frontend.json")])
            else:
                cids.add(name[: -len(".json")])

        for cid in cids:
            meta = self._reconstruct_meta(cid)
            if meta is not None:
                index[cid] = meta
        return index

    def _reconstruct_meta(self, cid: str) -> Optional[Dict]:
        """Build a best-effort index entry for *cid* from its message files."""
        backend = self._messages_path(cid)
        frontend = self._frontend_messages_path(cid)

        if not backend.exists() and not frontend.exists():
            return None

        messages: List[Dict] = []
        for src in (backend, frontend):
            if not src.exists():
                continue
            try:
                with open(src, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, list) and loaded:
                    messages = loaded
                    break
            except (json.JSONDecodeError, OSError):
                continue

        # Time is STORAGE, not inference. Prefer the per-message ``ts`` that
        # save_messages stamps onto every message on first persist — created_at
        # = earliest ts, updated_at = latest ts. This survives copy/migrate
        # unchanged (it's data inside the file, not the file's inode), and it
        # is the real time the conversation happened. Only when the messages
        # carry no ts at all (legitimate legacy data predating the stamping)
        # do we fall back to filesystem mtime — and then we flag
        # ``recovered_time=True`` so readers know these timestamps are a
        # best-effort inference, not stored fact.
        tss: List[str] = []
        for m in messages:
            if isinstance(m, dict):
                t = m.get("ts")
                if isinstance(t, str) and t:
                    tss.append(t)

        inferred_time = not tss
        if tss:
            created = min(tss)
            updated = max(tss)
        else:
            # Legacy fallback ONLY — pre-stamping data has no stored ts, so
            # mtime (content-change time) is the least-bad proxy for when the
            # file last mattered. ctime is NOT used: it tracks inode metadata
            # changes (chmod/chown/rename) and is regularly pushed ahead of
            # mtime by operations unrelated to the conversation, producing the
            # "created AFTER updated" inversion the old code had to paper over.
            mtimes = []
            for src in (backend, frontend):
                if src.exists():
                    try:
                        mtimes.append(src.stat().st_mtime)
                    except OSError:
                        pass
            if mtimes:
                created = datetime.fromtimestamp(max(mtimes), timezone.utc).isoformat()
                updated = created
            else:
                created = updated = datetime.now(timezone.utc).isoformat()

        meta_out = {
            "id": cid,
            "parent_id": None,
            "session_id": None,
            "type": "user_chat",
            "status": "completed",
            "provider_id": None,
            "created_at": created,
            "updated_at": updated,
            "title": _extract_title(messages) if messages else "Untitled",
            "recovered": True,
        }
        if inferred_time:
            meta_out["recovered_time"] = True
        return meta_out

    def _save_index(self) -> None:
        """Caller must hold self._lock."""
        _atomic_write_json(self._index_path(), self._index)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_conversation(
        self,
        conversation_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        provider_id: Optional[str] = None,
        conv_type: str = "user_chat",
    ) -> str:
        """
        Create a new conversation and return its ID.

        Idempotent: if *conversation_id* already exists, returns it unchanged
        (supports the resume / continue flow).

        The ``title`` key is intentionally NOT set here — it is only ever
        extracted from the actual message list by ``save_messages`` or
        ``save_frontend_messages``.
        """
        cid = conversation_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            if cid in self._index:
                return cid

            self._index[cid] = {
                "id": cid,
                "parent_id": parent_id,
                "session_id": session_id,
                "type": conv_type,
                "status": "running",
                "provider_id": provider_id,
                "created_at": now,
                "updated_at": now,
            }
            self._save_index()

        logger.info(f"[store] Created conversation {cid[:8]}... (type={conv_type})")
        return cid

    def save_messages(self, conversation_id: str, messages: List[Dict]) -> None:
        """
        Full-replace the stored message list for a conversation.

        Always extracts the title from the first user message and sets it
        in the index.  This is the primary path for title extraction — the
        title key does not exist at all until this point (or until
        ``save_frontend_messages`` is called).

        STAMPS a ``ts`` (arrival time, UTC ISO-8601) onto every message that
        doesn't already carry one on the FIRST persist of that message list.
        Time is part of storage, not an inference — every conversation's
        when-it-happened is read straight back from these per-message ts,
        never from filesystem ctime/mtime (which track inode/content changes,
        not when a user actually said something, and which chmod/cp -p/tar -p
        reshape at will). The full list is re-saved many times during a stream,
        so this is the single natural choke point — no caller has to remember
        to stamp. Already-stamped messages keep their real arrival time and
        are never overwritten by a later now.
        """
        now = datetime.now(timezone.utc).isoformat()
        for msg in messages:
            if isinstance(msg, dict) and not msg.get("ts"):
                msg["ts"] = now

        with self._lock:
            meta = self._index.get(conversation_id)
            if meta is None:
                raise KeyError(f"Conversation {conversation_id} not found")

            meta["title"] = _extract_title(messages)

            meta["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save_index()

        _atomic_write_json(self._messages_path(conversation_id), messages)
        self._unlink_if_missing_from_index(conversation_id)
        logger.info(
            f"[store] Saved {len(messages)} messages for {conversation_id[:8]}..."
        )

    def update_status(self, conversation_id: str, status: str) -> None:
        """Update the status field of a conversation."""
        with self._lock:
            meta = self._index.get(conversation_id)
            if meta is None:
                raise KeyError(f"Conversation {conversation_id} not found")
            meta["status"] = status
            meta["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save_index()

    def get_conversation(self, conversation_id: str) -> Dict[str, Any]:
        """Return metadata **and** messages for a single conversation."""
        with self._lock:
            meta = self._index.get(conversation_id)
            if meta is None:
                raise KeyError(f"Conversation {conversation_id} not found")
            meta = dict(meta)

        messages = self.get_messages(conversation_id)
        return {**meta, "messages": messages}

    def get_messages(self, conversation_id: str) -> List[Dict]:
        """Return the stored message list (for feeding back to the model)."""
        path = self._messages_path(conversation_id)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to read messages for {conversation_id}: {e}")
            return []

    def list_conversations(
        self,
        conv_type: Optional[str] = None,
        parent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[Dict]:
        """
        List conversation summaries (metadata only, no messages).

        Supports filtering by type, parent_id, session_id.
        Sorted by updated_at descending.
        """
        with self._lock:
            items = list(self._index.values())

        if conv_type is not None:
            items = [c for c in items if c.get("type") == conv_type]
        if parent_id is not None:
            items = [c for c in items if c.get("parent_id") == parent_id]
        if session_id is not None:
            items = [c for c in items if c.get("session_id") == session_id]

        items.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        return items

    def get_children(self, conversation_id: str) -> List[Dict]:
        """List child conversations (subagents) spawned by this conversation."""
        return self.list_conversations(parent_id=conversation_id)

    def ids_in_subtree(self, conversation_id: str) -> List[str]:
        """Return *conversation_id* followed by every descendant id.

        Walks ``parent_id`` links transitively so a top-level delete cannot
        leave orphaned subagent transcripts in the index. Raises ``KeyError``
        if the root id is not stored.
        """
        with self._lock:
            return self._ids_in_subtree_locked(conversation_id)

    def _ids_in_subtree_locked(self, conversation_id: str) -> List[str]:
        """Caller must hold ``self._lock``."""
        if conversation_id not in self._index:
            raise KeyError(f"Conversation {conversation_id} not found")
        children_by_parent: Dict[str, List[str]] = {}
        for cid, meta in self._index.items():
            parent = meta.get("parent_id") if isinstance(meta, dict) else None
            if parent:
                children_by_parent.setdefault(parent, []).append(cid)
        ordered: List[str] = []
        stack = [conversation_id]
        seen = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            ordered.append(current)
            for child in children_by_parent.get(current, ()):
                stack.append(child)
        return ordered

    def _unlink_if_missing_from_index(self, conversation_id: str) -> None:
        """Drop files written after DELETE won the race against a late persist."""
        with self._lock:
            if conversation_id in self._index:
                return
        for path in (
            self._messages_path(conversation_id),
            self._frontend_messages_path(conversation_id),
        ):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass

    def save_frontend_messages(self, conversation_id: str, messages: List[Dict]) -> None:
        """Save frontend-formatted messages to a separate file.

        If the conversation does not yet have a title, one is extracted from
        the provided messages and persisted into the index.  This is the
        first opportunity to set a title when the frontend seeds a user
        message before the backend starts streaming.

        STAMPS a ``ts`` (arrival time, UTC ISO-8601) exactly like
        ``save_messages`` — time is storage, not inference here too. A
        conversation first seeded from the frontend must carry real
        timestamps so ``_reconstruct_meta`` reads fact, not mtime, and so a
        frontend-seeded conversation that later gets a backend counterpart
        stays time-consistent across both files.
        """
        now = datetime.now(timezone.utc).isoformat()
        sanitize_frontend_messages(messages)
        for msg in messages:
            if isinstance(msg, dict) and not msg.get("ts"):
                msg["ts"] = now

        # Try to extract a title before writing the file — if the
        # conversation is brand new there won't be one yet.
        with self._lock:
            meta = self._index.get(conversation_id)
            if meta is None:
                # Deleted (or never created). A late persist after DELETE
                # must not resurrect an orphan frontend.json.
                return
            if "title" not in meta:
                meta["title"] = _extract_title(messages)
                meta["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._save_index()

        _atomic_write_json(self._frontend_messages_path(conversation_id), messages)
        self._unlink_if_missing_from_index(conversation_id)

    def seed_frontend_user_message(self, conversation_id: str, raw_content: str) -> None:
        """Persist a user bubble before the first SSE event.

        Strips transport wrappers from the visible text and keeps chips
        on ``taskInstruction`` / ``attachedFiles``, so a reload still
        shows what was applied.
        """
        new_user_msg = user_message_for_frontend(raw_content)
        existing = self.get_frontend_messages(conversation_id)
        if existing:
            existing.append(new_user_msg)
            self.save_frontend_messages(conversation_id, existing)
        else:
            self.save_frontend_messages(conversation_id, [new_user_msg])

    def get_frontend_messages(self, conversation_id: str) -> List[Dict]:
        """Read frontend-formatted messages, returning [] if not persisted."""
        path = self._frontend_messages_path(conversation_id)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                return sanitize_frontend_messages(loaded)
            return loaded
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to read frontend messages for {conversation_id}: {e}")
            return []

    def delete_conversation(self, conversation_id: str) -> List[str]:
        """Delete a conversation and every descendant.

        Returns the deleted ids with the requested conversation first.
        Index is updated before files are unlinked so a racing stream
        ``finally`` persist (``update_status`` / ``save_messages``) hits
        ``KeyError`` instead of resurrecting the chat.
        """
        with self._lock:
            deleted_ids = self._ids_in_subtree_locked(conversation_id)
            for cid in deleted_ids:
                self._index.pop(cid, None)
            self._save_index()

        for cid in deleted_ids:
            msg_path = self._messages_path(cid)
            if msg_path.exists():
                msg_path.unlink()
            fe_path = self._frontend_messages_path(cid)
            if fe_path.exists():
                fe_path.unlink()
            logger.info(f"[store] Deleted conversation {cid[:8]}...")
        return deleted_ids


# ── Module-level singleton ──────────────────────────────────────────────────

store = ConversationStore()
