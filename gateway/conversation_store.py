"""
Conversation Store — JSON file-based storage for conversation history.

Stores conversation metadata in an index file and full message lists in
individual JSON files.  Uses atomic writes (temp file + rename) to prevent
corruption on crash.

Thread-safe: all public methods acquire self._lock before mutating state.
"""

import copy
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

from src.message_provenance import TURN_INPUT_RECEIPT
from gateway.attached_files import (
    ATTACHED_FILES_START,
    apply_attached_files,
    extract_attached_files,
    strip_attached_files,
)
from gateway.task_instruction_display import (
    TASK_INSTRUCTION_END,
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
GATEWAY_RESTARTED = "gateway_restarted"
TURN_WRITE_INTERRUPTED = "turn_write_interrupted"
_PENDING_TURN_ID = "pending_turn_id"

TITLE_MAX_LENGTH = 100
_CONVERSATION_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}\Z")
_WINDOWS_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])\Z")
_MEMORY_TRACE_ID = re.compile(
    r"memory-maintenance:(extraction|consolidation):maint-\1-[a-f0-9]{10}\Z"
)
_PORTABLE_TRACE_PREFIX = "~memory-maintenance~"


class InvalidConversationId(ValueError):
    """An ID that cannot be used as an unambiguous transcript filename."""


def require_conversation_id(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidConversationId("Invalid conversation ID")
    if _MEMORY_TRACE_ID.fullmatch(value):
        return value
    if not _CONVERSATION_ID.fullmatch(value):
        raise InvalidConversationId("Invalid conversation ID")
    folded = value.lower()
    if (folded == "index" or folded.startswith("index.corrupt-") or folded.endswith(".frontend")
            or _WINDOWS_DEVICE.fullmatch(folded.split(".", 1)[0])):
        raise InvalidConversationId("Reserved conversation ID")
    return value


def messages_for_retry(
    messages: Optional[List[Dict]],
    user_text: str = "",
    *,
    frontend_messages: Optional[List[Dict]] = None,
) -> List[Dict]:
    """Keep the stored transcript and recover a pending turn from older histories.

    Incomplete tool rounds are made sendable by ``_fix_orphan_tool_calls``
    on the chat path — this helper does not re-trim them.
    """
    msgs = list(messages or [])
    if frontend_messages and frontend_messages[-1].get("isError"):
        visible_users = [msg for msg in frontend_messages if msg.get("role") == "user"]
        raw_users = [user_message_for_frontend(msg.get("content"))["content"]
                     for msg in msgs if msg.get("role") == "user"]
        if len(visible_users) == len(raw_users) + 1 and raw_users == [
            msg.get("content", "") for msg in visible_users[:-1]
        ]:
            pending = visible_users[-1]
            text = pending.get("content", "")
            instruction = pending.get("taskInstruction")
            if instruction:
                text = f"{TASK_INSTRUCTION_START}\n{instruction}\n{TASK_INSTRUCTION_END}\n\n{text}"
            text = apply_attached_files(text, pending.get("attachedFiles") or [])
            msg = {"role": "user", "content": text}
            if pending.get("ts"):
                msg["ts"] = pending["ts"]
            msgs.append(msg)
    if msgs:
        return msgs
    if user_text:
        return [{"role": "user", "content": user_text}]
    return []


class InvalidForkPoint(ValueError):
    """``before_user_message`` does not name a stored user-message boundary."""


def _drop_incomplete_tool_rounds(raw_messages: list) -> list:
    """Remove orphaned tool rounds left by older versions of the gateway.

    A provider rejects an assistant ``tool_calls`` message that is not
    followed by exactly one result per call, so every copy of a transcript
    (fork, resume-from-storage) must drop a half-finished round instead of
    forwarding it. This lives next to storage because both the chat route and
    ``ConversationStore.fork_conversation`` need it.
    """
    complete = []
    i = 0
    while i < len(raw_messages):
        message = raw_messages[i]
        i += 1
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        if calls:
            start = i
            while i < len(raw_messages) and raw_messages[i].get("role") == "tool":
                i += 1
            expected = {call.get("id") for call in calls}
            results = raw_messages[start:i]
            if (
                all(expected) and len(expected) == len(calls) == len(results)
                and expected == {result.get("tool_call_id") for result in results}
            ):
                complete.extend([message, *results])
        elif message.get("role") != "tool":
            complete.append(message)
    return complete


def _user_message_indices(messages: List[Dict]) -> List[int]:
    """Positions of the user turns in *messages* (sparse: tool rounds intervene)."""
    return [
        i for i, msg in enumerate(messages)
        if isinstance(msg, dict) and msg.get("role") == "user"
    ]


def slice_history_for_fork(
    raw_messages: Optional[List[Dict]],
    frontend_messages: Optional[List[Dict]],
    before_user_message: Optional[int] = None,
) -> tuple:
    """Split stored history at a user-message boundary — no mutation.

    *before_user_message* is a zero-based ordinal over the STORED user
    messages of the provider transcript; everything strictly before that
    message is kept. ``None`` keeps all safely persisted history and ``0`` is
    a valid empty chat. A stale/out-of-range ordinal raises
    :class:`InvalidForkPoint` instead of silently copying the wrong prefix.

    The frontend list is cut at the same ordinal even though its shape
    differs (one bubble per step, no tool messages), so both halves of the
    copy end on the same user turn.
    """
    raw = list(raw_messages or [])
    frontend = list(frontend_messages or [])
    if before_user_message is None:
        return raw, frontend
    if not isinstance(before_user_message, int) or isinstance(before_user_message, bool):
        raise InvalidForkPoint("before_user_message must be an integer")

    raw_users = _user_message_indices(raw)
    if before_user_message < 0 or before_user_message > len(raw_users):
        raise InvalidForkPoint(
            f"before_user_message {before_user_message} is out of range "
            f"for {len(raw_users)} stored user message(s)"
        )
    if before_user_message == 0:
        return [], []

    raw_cut = raw_users[before_user_message] if before_user_message < len(raw_users) else len(raw)
    frontend_users = _user_message_indices(frontend)
    frontend_cut = (
        frontend_users[before_user_message]
        if before_user_message < len(frontend_users)
        else len(frontend)
    )
    return raw[:raw_cut], frontend[:frontend_cut]


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


def frontend_messages_for_status(frontend_messages: List[Dict], conversation: dict) -> List[Dict]:
    if conversation.get("status") != "error":
        return frontend_messages
    recovery_reason = conversation.get("recovery_reason")
    if recovery_reason not in {GATEWAY_RESTARTED, TURN_WRITE_INTERRUPTED}:
        return ensure_error_frontend_message(frontend_messages)

    visible = frontend_messages or sanitize_frontend_messages([
        {"role": msg["role"], "content": msg.get("content", "")}
        for msg in conversation.get("messages", [])
        if isinstance(msg, dict) and msg.get("role") in ("user", "assistant")
    ])
    detail = (
        "AuroraCoder stopped while saving this turn. Use Try Again to continue the same conversation."
        if recovery_reason == TURN_WRITE_INTERRUPTED
        else "AuroraCoder restarted before this turn finished. Use Try Again to continue the same conversation."
    )
    return ensure_error_frontend_message(visible, {"message": detail, "type": recovery_reason})


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


def _message_file_signature(st: os.stat_result) -> tuple:
    """Identity of one stored message file version.

    Used by derived-read caches to decide whether cached data still matches
    what is on disk. Atomic saves replace the inode; an in-place overwrite
    changes mtime and ctime even when the size is unchanged, so edits are
    never mistaken for the cached version.
    """
    return (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


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
        from gateway.task_checklist import TaskChecklistStore

        self._dir = storage_dir or DEFAULT_STORAGE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._index: Dict[str, Dict] = {}
        self._load_index()
        self.tasks = TaskChecklistStore(self._dir / "tasks", _atomic_write_json, self.get_messages)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _index_path(self) -> Path:
        return self._dir / "index.json"

    def _conversation_path(self, conversation_id: str, suffix: str) -> Path:
        cid = require_conversation_id(conversation_id)
        if _MEMORY_TRACE_ID.fullmatch(cid):
            # Older Linux stores used the logical trace ID directly on disk.
            if os.name != "nt":
                legacy = self._dir / f"{cid}{suffix}"
                if legacy.is_file():
                    return legacy
            return self._dir / f"~{cid.replace(':', '~')}{suffix}"
        return self._dir / f"{cid}{suffix}"

    def _messages_path(self, conversation_id: str) -> Path:
        return self._conversation_path(conversation_id, ".json")

    def _frontend_messages_path(self, conversation_id: str) -> Path:
        return self._conversation_path(conversation_id, ".frontend.json")

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
        files. Only memory maintenance has a recognizable ID. Others default
        to user chats. All recovered entries are marked completed and recovered.
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
                stem = name[: -len(".frontend.json")]
            else:
                stem = name[: -len(".json")]
            if stem.startswith(_PORTABLE_TRACE_PREFIX):
                stem = stem[1:].replace("~", ":")
            cids.add(stem)

        for cid in cids:
            try:
                meta = self._reconstruct_meta(cid)
            except InvalidConversationId:
                continue
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
            "type": "memory_maintenance" if _MEMORY_TRACE_ID.fullmatch(cid) else "user_chat",
            "status": "completed",
            "provider_id": None,
            # Provenance lives in the index only; a rebuild cannot recover it.
            "forked_from_id": None,
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

    @property
    def storage_dir(self) -> Path:
        """Directory holding ``index.json`` and the per-conversation files."""
        return self._dir

    def create_conversation(
        self,
        conversation_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        provider_id: Optional[str] = None,
        conv_type: str = "user_chat",
        forked_from_id: Optional[str] = None,
        status: str = "running",
    ) -> str:
        """
        Create a new conversation and return its ID.

        Idempotent: if *conversation_id* already exists, returns it unchanged
        (supports the resume / continue flow).

        *forked_from_id* records durable provenance for a chat copied out of
        another transcript. It is deliberately a separate field from
        ``parent_id``: ``parent_id`` means "subagent of" and cascades on
        delete, while a fork is an independent chat that must outlive its
        source.

        *status* is the initial status; a caller that already knows the final
        one (a fork of completed history) passes it so the new chat never
        flashes as "running".

        The ``title`` key is intentionally NOT set here — it is only ever
        extracted from the actual message list by ``save_messages`` or
        ``save_frontend_messages``.
        """
        cid = str(uuid.uuid4()) if conversation_id in (None, "") else require_conversation_id(conversation_id)
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            if cid in self._index:
                return cid
            if any(key.lower() == cid.lower() for key in self._index):
                raise InvalidConversationId("Conversation ID differs only in letter case")

            self._index[cid] = {
                "id": cid,
                "parent_id": parent_id,
                "session_id": session_id,
                "type": conv_type,
                "status": status,
                "provider_id": provider_id,
                "forked_from_id": forked_from_id,
                "created_at": now,
                "updated_at": now,
            }
            self._save_index()

        logger.info(f"[store] Created conversation {cid[:8]}... (type={conv_type})")
        return cid

    def save_messages(self, conversation_id: str, messages: List[Dict], *, pending_turn_id: str | None = None) -> None:
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
            if isinstance(msg, dict):
                msg.pop(_PENDING_TURN_ID, None)
                if not msg.get("ts"):
                    msg["ts"] = now
        if pending_turn_id:
            # Keep the receipt beside the new raw user message until the gateway
            # commits admission in the index. No provider call has started yet.
            messages = [*messages[:-1], {**messages[-1], _PENDING_TURN_ID: pending_turn_id}]

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

    def has_turn_input(self, conversation_id: str, receipt: str) -> bool:
        with self._lock:
            return receipt in self._index.get(conversation_id, {}).get("turn_inputs", {})

    def queue_turn_input(self, conversation_id: str, receipt: str, content: str) -> None:
        with self._lock:
            meta = self._index[conversation_id]
            if receipt in meta.get("turn_inputs", {}):
                return
            inputs = {**meta.get("turn_inputs", {}), receipt: content}
            updated = {**self._index, conversation_id: {**meta, "turn_inputs": inputs}}
            _atomic_write_json(self._index_path(), updated)
            self._index = updated

    def pending_turn_inputs(self, conversation_id: str) -> list[dict]:
        with self._lock:
            inputs = dict(self._index.get(conversation_id, {}).get("turn_inputs", {}))
        if not any(inputs.values()):
            return []
        persisted = {message.get(TURN_INPUT_RECEIPT) for message in self.get_messages(conversation_id)}
        return [
            {"role": "user", "content": content, TURN_INPUT_RECEIPT: receipt}
            for receipt, content in inputs.items() if content is not None and receipt not in persisted
        ]

    def acknowledge_turn_inputs(self, conversation_id: str, messages: list[dict]) -> None:
        """Keep accepted receipts, releasing the payload after transcript persistence."""
        with self._lock:
            meta = self._index[conversation_id]
            inputs = meta.get("turn_inputs", {})
            released = self._released_turn_inputs(inputs, messages)
            if released == inputs:
                return
            updated = {**self._index, conversation_id: {**meta, "turn_inputs": released}}
            _atomic_write_json(self._index_path(), updated)
            self._index = updated

    @staticmethod
    def _released_turn_inputs(inputs: dict, messages: list[dict]) -> dict:
        applied = {message.get(TURN_INPUT_RECEIPT) for message in messages}
        return {receipt: None if receipt in applied else content for receipt, content in inputs.items()}

    _KEEP_ACCEPTED_TURN = object()

    def update_status(
        self, conversation_id: str, status: str, *, accepted_turn_id=_KEEP_ACCEPTED_TURN,
        recovery_reason: str | None = None,
    ) -> None:
        """Update status and optionally replace the current turn receipt."""
        with self._lock:
            meta = self._index.get(conversation_id)
            if meta is None:
                raise KeyError(f"Conversation {conversation_id} not found")
            meta = {**meta, "status": status}
            if accepted_turn_id is not self._KEEP_ACCEPTED_TURN:
                receipts = meta.get("accepted_turn_ids", [])
                for receipt in (meta.get("accepted_turn_id"), accepted_turn_id):
                    if receipt and receipt not in receipts:
                        receipts = [*receipts, receipt]
                if receipts:
                    meta["accepted_turn_ids"] = receipts
            if accepted_turn_id is None:
                meta.pop("accepted_turn_id", None)
            elif accepted_turn_id is not self._KEEP_ACCEPTED_TURN:
                meta["accepted_turn_id"] = accepted_turn_id
            if recovery_reason:
                meta["recovery_reason"] = recovery_reason
            elif status != "error":
                meta.pop("recovery_reason", None)
            meta["updated_at"] = datetime.now(timezone.utc).isoformat()
            updated = {**self._index, conversation_id: meta}
            _atomic_write_json(self._index_path(), updated)
            self._index = updated

    def recover_turn_write(self, conversation_id: str) -> None:
        """Recover a user message written just before the gateway could record admission."""
        raw_users = [
            message for message in self._read_messages(conversation_id)
            if isinstance(message, dict) and message.get("role") == "user"
        ]
        receipt = raw_users[-1].get(_PENDING_TURN_ID) if raw_users else None
        if not receipt or self.turn_state(conversation_id, receipt) != "unaccepted":
            return
        visible_users = [m.get("content", "") for m in self.get_frontend_messages(conversation_id)
                         if m.get("role") == "user"]
        preceding = [user_message_for_frontend(m.get("content"))["content"] for m in raw_users[:-1]]
        if visible_users == preceding:
            self.seed_frontend_user_message(conversation_id, raw_users[-1]["content"])
        self.update_status(conversation_id, "error", accepted_turn_id=receipt, recovery_reason=TURN_WRITE_INTERRUPTED)

    def turn_state(self, conversation_id: str, turn_id: str) -> str:
        """Distinguish an unaccepted turn from the current or a superseded one."""
        with self._lock:
            meta = self._index.get(conversation_id, {})
            if meta.get("accepted_turn_id") == turn_id:
                return "current"
            if turn_id in meta.get("accepted_turn_ids", []):
                return "superseded"
        return "unaccepted"

    def recover_running_after_restart(self) -> int:
        """Make unfinished turns from the prior gateway process retryable."""
        with self._lock:
            interrupted = {
                cid: {**meta, "status": "error", "recovery_reason": GATEWAY_RESTARTED}
                for cid, meta in self._index.items()
                if isinstance(meta, dict) and meta.get("status") == "running"
            }
            if interrupted:
                recovered = {**self._index, **interrupted}
                _atomic_write_json(self._index_path(), recovered)
                self._index = recovered
            return len(interrupted)

    def mark_continued(self, conversation_id: str, next_id: str, *, inputs: list[dict] | None = None) -> None:
        """Commit the successor and release transferred inputs after both transcripts are saved."""
        with self._lock:
            if conversation_id == next_id or next_id not in self._index:
                raise ValueError("The continuation must be a separate saved conversation.")
            entry = {
                **self._index[conversation_id],
                "status": "continued",
                "continued_to": next_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if inputs:
                entry["turn_inputs"] = self._released_turn_inputs(entry.get("turn_inputs", {}), inputs)
            updated = {**self._index, conversation_id: entry}
            _atomic_write_json(self._index_path(), updated)
            self._index = updated

    def latest_continuation(self, conversation_id: str) -> str | None:
        with self._lock:
            visited = {conversation_id}
            current = self._index.get(conversation_id, {})
            latest = None
            while current.get("status") == "continued":
                next_id = current.get("continued_to")
                if not isinstance(next_id, str) or next_id in visited or next_id not in self._index:
                    break
                visited.add(next_id)
                latest = next_id
                current = self._index[next_id]
            return latest

    def get_conversation(self, conversation_id: str) -> Dict[str, Any]:
        """Return metadata **and** messages for a single conversation."""
        with self._lock:
            meta = self._index.get(conversation_id)
            if meta is None:
                raise KeyError(f"Conversation {conversation_id} not found")
            meta = dict(meta)
            meta.pop("accepted_turn_ids", None)
            meta.pop("turn_inputs", None)

        messages = self.get_messages(conversation_id)
        return {**meta, "messages": messages}

    def get_messages(self, conversation_id: str) -> List[Dict]:
        """Return the stored message list (for feeding back to the model)."""
        messages = self._read_messages(conversation_id)
        for message in messages:
            if isinstance(message, dict):
                message.pop(_PENDING_TURN_ID, None)
        return messages

    def _read_messages(self, conversation_id: str) -> List[Dict]:
        path = self._messages_path(conversation_id)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to read messages for {conversation_id}: {e}")
            return []

    def messages_signature(self, conversation_id: str) -> Optional[tuple]:
        """Cheap identity of the current message file, or ``None`` if absent.

        A derived-read cache validates its entry against this value before
        using it. Any of replace/write/unlink changes the signature.
        """
        try:
            return _message_file_signature(self._messages_path(conversation_id).stat())
        except OSError:
            return None

    def read_messages_snapshot(
        self, conversation_id: str
    ) -> tuple[Optional[tuple], List[Dict]]:
        """Read messages together with the identity of the bytes actually read.

        The signature comes from the open descriptor, so a concurrent atomic
        replace cannot label old content with the new file's identity. Returns
        ``(None, [])`` when the file is missing, unreadable or not a list.
        """
        path = self._messages_path(conversation_id)
        try:
            with open(path, "rb") as f:
                signature = _message_file_signature(os.fstat(f.fileno()))
                raw = f.read()
        except OSError:
            return None, []
        try:
            messages = json.loads(raw)
        except ValueError as e:
            logger.error(f"Failed to read messages for {conversation_id}: {e}")
            return None, []
        if not isinstance(messages, list):
            logger.error(
                f"Failed to read messages for {conversation_id}: expected a list"
            )
            return None, []
        return signature, messages

    def fork_conversation(
        self,
        conversation_id: str,
        before_user_message: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Copy a stored transcript into a NEW conversation and persist it now.

        The copy is what lets a client (or another session, or automation)
        continue earlier work without replaying raw history: it is complete on
        disk before this returns, so a reload or a gateway restart finds it.

        *before_user_message* is a zero-based ordinal over the stored user
        messages — everything before that turn is copied, ``None`` copies all
        safely persisted history, and ``0`` yields a valid empty chat. The
        assistant ``tool_calls``/``tool`` rounds are copied verbatim, minus any
        half-finished round the provider would reject.

        Provenance is stored as ``forked_from_id``; ``parent_id`` stays empty
        because that field means "subagent of" and cascades on delete.

        Raises ``KeyError`` for an unknown source and :class:`InvalidForkPoint`
        for an out-of-range ordinal.
        """
        with self._lock:
            source = self._index.get(conversation_id)
            if source is None:
                raise KeyError(f"Conversation {conversation_id} not found")
            source = dict(source)
            raw_source = self.get_messages(conversation_id)
            frontend_source = self.get_frontend_messages(conversation_id)

        raw_slice, frontend_slice = slice_history_for_fork(
            raw_source, frontend_source, before_user_message,
        )
        # Deep-copy so the caller can never share (or stamp) the source's
        # message objects, and prune incomplete rounds so the copy is a valid
        # provider transcript.
        raw_slice = _drop_incomplete_tool_rounds(copy.deepcopy(raw_slice))
        frontend_slice = copy.deepcopy(frontend_slice)

        status = source.get("status")
        if status not in TERMINAL_STATUSES or len(raw_slice) < len(raw_source):
            # A stale "running" row (gateway killed mid-turn) must not make the
            # copy look live; the copy is a stable snapshot by construction.
            status = "completed"

        new_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        sanitize_frontend_messages(frontend_slice)
        for message in (*raw_slice, *frontend_slice):
            if isinstance(message, dict) and not message.get("ts"):
                message["ts"] = now
        meta = {
            "id": new_id, "parent_id": None, "session_id": None, "type": "user_chat",
            "status": status, "provider_id": source.get("provider_id"),
            "forked_from_id": conversation_id, "created_at": now, "updated_at": now,
            "title": _extract_title(raw_slice),
        }
        files = (self._messages_path(new_id), self._frontend_messages_path(new_id))
        with self._lock:
            try:
                _atomic_write_json(files[0], raw_slice)
                _atomic_write_json(files[1], frontend_slice)
                self._index[new_id] = meta
                self._save_index()
            except OSError:
                self._index.pop(new_id, None)
                for path in files:
                    path.unlink(missing_ok=True)
                raise

        record = self.get_conversation(new_id)
        logger.info(
            f"[store] Forked {conversation_id[:8]}... -> {new_id[:8]}... "
            f"({len(raw_slice)} messages, before_user_message={before_user_message})"
        )
        return {
            "id": new_id,
            "forked_from_id": conversation_id,
            "title": record.get("title", "Untitled"),
            "status": status,
            "provider_id": source.get("provider_id"),
            "messages": record["messages"],
            "frontend_messages": self.get_frontend_messages(new_id),
        }

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
            items = [
                {key: value for key, value in meta.items() if key not in {"accepted_turn_ids", "turn_inputs"}}
                for meta in self._index.values()
            ]

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

    def seed_frontend_user_message(self, conversation_id: str, raw_content: str, *, source: str | None = None) -> None:
        """Persist a user bubble before the first SSE event.

        Strips transport wrappers from the visible text and keeps chips
        on ``taskInstruction`` / ``attachedFiles``, so a reload still
        shows what was applied.
        """
        new_user_msg = user_message_for_frontend(raw_content)
        if source:
            new_user_msg["source"] = source
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
            try:
                msg_path = self._messages_path(cid)
            except InvalidConversationId:
                # An old invalid entry can be deleted from the index without following its path.
                continue
            if msg_path.exists():
                msg_path.unlink()
            fe_path = self._frontend_messages_path(cid)
            if fe_path.exists():
                fe_path.unlink()
            logger.info(f"[store] Deleted conversation {cid[:8]}...")
        return deleted_ids


# ── Module-level singleton ──────────────────────────────────────────────────

store = ConversationStore()
