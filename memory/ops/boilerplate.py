"""Task-instruction and continue-as-new-chat handoff text is session
scaffold, not a user-stated standing memory. Often marked ignore-if-
irrelevant, and a later user line can contradict the handoff (e.g. push).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from gateway.task_instruction_display import (
    TASK_INSTRUCTION_START,
    extract_task_instruction,
    strip_task_instruction,
)
from memory.ops.similarity import tokens

logger = logging.getLogger(__name__)

HANDOFF_PREFIX = "[Continued from previous agent session]"
_ECHO_CONTAINMENT = 0.5
_USER_RESTATEMENT = 0.5
_SENTENCE_RE = re.compile(r"[^\n.!?]+")
_purge_done_for: set[int] = set()


def is_handoff_message(content: str) -> bool:
    return bool(content) and content.lstrip().startswith(HANDOFF_PREFIX)


def split_user_content(content: str) -> Tuple[str, str]:
    """Return ``(scaffold_text, genuine_user_text)`` for one user message."""
    if not content:
        return "", ""
    if is_handoff_message(content):
        return content.strip(), ""
    if TASK_INSTRUCTION_START in content:
        return (extract_task_instruction(content) or "").strip(), strip_task_instruction(content)
    return "", content.strip()


def collect_scaffold_and_genuine(messages: Sequence[Dict[str, Any]]) -> Tuple[str, str]:
    scaffolds: List[str] = []
    genuines: List[str] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        scaffold, genuine = split_user_content(msg.get("content") or "")
        if scaffold:
            scaffolds.append(scaffold)
        if genuine:
            genuines.append(genuine)
    return "\n".join(scaffolds), "\n".join(genuines)


def render_user_for_extraction(content: str) -> List[str]:
    """Omit/label scaffold so the write-pass judge does not see it as USER speech."""
    scaffold, genuine = split_user_content(content)
    lines: List[str] = []
    if is_handoff_message(content):
        lines.append(
            "HANDOFF: agent-authored one-shot session context "
            "(not a user standing rule; do not mine preferences, "
            "conventions, or landmines from it)."
        )
    if genuine:
        lines.append(f"USER: {genuine}")
    elif scaffold and not is_handoff_message(content):
        lines.append(
            "USER: (session task instruction only — ignore-if-irrelevant "
            "scaffolding, not a user request)"
        )
    return lines


def _containment(candidate: str, haystack: str) -> float:
    cand, hay = tokens(candidate), tokens(haystack)
    if not cand:
        return 0.0
    return len(cand & hay) / len(cand)


def _max_sentence_containment(candidate: str, haystack: str) -> float:
    best = 0.0
    for raw in _SENTENCE_RE.findall(haystack):
        sent = raw.strip()
        if len(sent) < 8:
            continue
        best = max(best, _containment(candidate, sent), _containment(sent, candidate))
    return best


def is_scaffold_echo(candidate_text: str, scaffold: str, genuine: str) -> bool:
    """True when the candidate restates scaffold and the user did not also say it."""
    if not candidate_text.strip() or not scaffold.strip():
        return False
    if (
        _containment(candidate_text, genuine) >= _USER_RESTATEMENT
        or _max_sentence_containment(candidate_text, genuine) >= _USER_RESTATEMENT
    ):
        return False
    return (
        _containment(candidate_text, scaffold) >= _ECHO_CONTAINMENT
        or _max_sentence_containment(candidate_text, scaffold) >= _ECHO_CONTAINMENT
    )


def purge_scaffold_echoes(repo, message_lists: Iterable[Sequence[Dict[str, Any]]]) -> List[str]:
    """Delete stored memories that only echo task-instruction / handoff text."""
    scaffolds: List[str] = []
    genuines: List[str] = []
    for messages in message_lists:
        sc, ge = collect_scaffold_and_genuine(messages)
        if sc:
            scaffolds.append(sc)
        if ge:
            genuines.append(ge)
    scaffold, genuine = "\n".join(scaffolds), "\n".join(genuines)
    if not scaffold.strip():
        return []
    deleted: List[str] = []
    for item in list(repo.all_items()):
        if is_scaffold_echo(f"{item.description}\n{item.content}", scaffold, genuine):
            if repo.delete(item.id):
                deleted.append(item.id)
                logger.info("[memory] Purged scaffold-echo memory %s (%s)", item.id, item.description)
    return deleted


def _load_user_chat_message_lists() -> List[List[Dict[str, Any]]]:
    try:
        from gateway.conversation_store import store
        lists: List[List[Dict[str, Any]]] = []
        for meta in store.list_conversations(conv_type="user_chat"):
            cid = meta.get("id")
            if cid:
                lists.append(store.get_messages(cid))
        return lists
    except Exception:
        logger.debug("[memory] Could not load conversations for scaffold purge", exc_info=True)
        return []


def ensure_scaffold_echoes_purged(repo, message_lists: Optional[Iterable[Sequence[Dict[str, Any]]]] = None) -> List[str]:
    """Once-per-repo purge so Settings / stance / recall drop already-written scaffold cards."""
    if message_lists is not None:
        return purge_scaffold_echoes(repo, message_lists)
    repo_id = id(repo)
    if repo_id in _purge_done_for:
        return []
    deleted = purge_scaffold_echoes(repo, _load_user_chat_message_lists())
    _purge_done_for.add(repo_id)
    return deleted
