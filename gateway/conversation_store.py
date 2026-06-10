"""
Conversation Store — JSON file-based storage for conversation history.

Stores conversation metadata in an index file and full message lists in
individual JSON files.  Uses atomic writes (temp file + rename) to prevent
corruption on crash.

Thread-safe: all public methods acquire self._lock before mutating state.
"""

import json
import os
import re
import uuid
import threading
import tempfile
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# Distinctive markers for task instruction blocks injected by the frontend.
# Real users will never type these, so stripping is reliable and does not
# affect the agent's behaviour (the markers flow to the LLM as natural text).
TASK_INSTRUCTION_START = "[TASK INSTRUCTION]"
TASK_INSTRUCTION_END = "[/TASK INSTRUCTION]"

# Regex that removes a task-instruction block (any content between the
# start/end markers, including newlines) and the blank line that follows it.
_TASK_BLOCK_RE = re.compile(
    re.escape(TASK_INSTRUCTION_START) + r".*?" + re.escape(TASK_INSTRUCTION_END) + r"\s*",
    re.DOTALL,
)

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


def strip_task_instruction(content: str) -> str:
    """Remove a task instruction marker block and trailing whitespace from *content*.

    Returns the cleaned string.  If no markers are present the input is
    returned unchanged (apart from leading/trailing whitespace).
    """
    return _TASK_BLOCK_RE.sub("", content).strip()


def _extract_title(messages: List[Dict]) -> str:
    """Extract a title from the first user message.

    Strips any frontend-injected task instruction block (bracketed by
    ``[TASK INSTRUCTION]`` / ``[/TASK INSTRUCTION]`` markers) so the
    title shows the actual user message, not the instruction prefix.

    Falls back to the last paragraph after a double newline only when
    no markers are present (legacy compatibility).
    """
    for msg in messages:
        if msg.get("role") == "user" and msg.get("content"):
            content = msg["content"].strip()

            # 1) Marked task instruction (new, reliable)
            if TASK_INSTRUCTION_START in content:
                content = strip_task_instruction(content)

            # 2) Legacy fallback: frontend used plain \n\n separator
            elif "\n\n" in content:
                content = content.rsplit("\n\n", 1)[-1]

            title = content.replace("\n", " ")
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

        ctimes, mtimes = [], []
        for src in (backend, frontend):
            if src.exists():
                try:
                    st = src.stat()
                    ctimes.append(st.st_ctime)
                    mtimes.append(st.st_mtime)
                except OSError:
                    pass

        if ctimes:
            created = datetime.fromtimestamp(min(ctimes), timezone.utc).isoformat()
            updated = datetime.fromtimestamp(max(mtimes), timezone.utc).isoformat()
        else:
            created = updated = datetime.now(timezone.utc).isoformat()

        return {
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
        """
        with self._lock:
            meta = self._index.get(conversation_id)
            if meta is None:
                raise KeyError(f"Conversation {conversation_id} not found")

            meta["title"] = _extract_title(messages)

            meta["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save_index()

        _atomic_write_json(self._messages_path(conversation_id), messages)
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

    def save_frontend_messages(self, conversation_id: str, messages: List[Dict]) -> None:
        """Save frontend-formatted messages to a separate file.

        If the conversation does not yet have a title, one is extracted from
        the provided messages and persisted into the index.  This is the
        first opportunity to set a title when the frontend seeds a user
        message before the backend starts streaming.
        """
        # Try to extract a title before writing the file — if the
        # conversation is brand new there won't be one yet.
        with self._lock:
            meta = self._index.get(conversation_id)
            if meta is not None and "title" not in meta:
                meta["title"] = _extract_title(messages)
                meta["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._save_index()

        _atomic_write_json(self._frontend_messages_path(conversation_id), messages)

    def get_frontend_messages(self, conversation_id: str) -> List[Dict]:
        """Read frontend-formatted messages, returning [] if not persisted."""
        path = self._frontend_messages_path(conversation_id)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to read frontend messages for {conversation_id}: {e}")
            return []

    def delete_conversation(self, conversation_id: str) -> None:
        """Delete a conversation's metadata and message file."""
        with self._lock:
            if conversation_id not in self._index:
                raise KeyError(f"Conversation {conversation_id} not found")
            del self._index[conversation_id]
            self._save_index()

        msg_path = self._messages_path(conversation_id)
        if msg_path.exists():
            msg_path.unlink()
        fe_path = self._frontend_messages_path(conversation_id)
        if fe_path.exists():
            fe_path.unlink()
        logger.info(f"[store] Deleted conversation {conversation_id[:8]}...")


# ── Module-level singleton ──────────────────────────────────────────────────

store = ConversationStore()
