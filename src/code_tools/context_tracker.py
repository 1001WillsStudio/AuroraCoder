"""
Context Tracker — the general form of the "Living Tool State" pattern.

Each ContextTracker:
  1. Scans message history to discover current state ("what's open?")
  2. Generates a display block with unique start/end markers
  3. Strips stale blocks from previous messages to save context
  4. Registers trigger tools that activate it

This replaces the hardcoded ``code_tool_called`` / ``toolstore_tool_called``
booleans with a generic registry.  Adding a new living-state context
(browser tabs, API connections, etc.) means implementing a ContextTracker
and registering it — zero changes to ``tool_executor.py`` or ``main_flow.py``.

See ``README.md`` §1 "Living Tool State" for the design philosophy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Set

# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class ContextTracker(ABC):
    """One kind of "living state" that the agent manages.

    Subclasses must provide:

    * ``name`` — unique identifier for this tracker
    * ``trigger_tools`` — set of tool names that activate it
    * ``block_start`` / ``block_end`` — unique markers for the display block
    * ``discover(messages)`` — scan history, return current state
    * ``render(state)`` — produce the display block
    """

    # -- override these ---------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def trigger_tools(self) -> Set[str]: ...

    @property
    @abstractmethod
    def block_start(self) -> str: ...

    @property
    @abstractmethod
    def block_end(self) -> str: ...

    @abstractmethod
    def discover(self, messages: List[Dict]) -> object:
        """Scan *messages* and return the current state.

        The return type is tracker-specific (e.g. ``Set[str]`` of open
        files).  It only needs to be understood by ``render()``.
        """
        ...

    @abstractmethod
    def render(self, state: object) -> str:
        """Produce a display block for *state*.

        Returns the full text including ``block_start`` / ``block_end``
        markers, or an empty string when there is nothing to show.
        """
        ...

    # -- helpers (rarely overridden) ---------------------------------------

    def strip_blocks(self, content: str) -> str:
        """Remove all instances of this tracker's block from *content*."""
        import re

        if not content:
            return content
        pattern = re.compile(
            re.escape(self.block_start) + r'.*?' + re.escape(self.block_end),
            re.DOTALL,
        )
        cleaned = pattern.sub('', content)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()

    def clean_previous_blocks(self, messages: List[Dict]) -> List[Dict]:
        """Strip stale display blocks from all tool messages (in-place)."""
        for msg in messages:
            if msg.get("role") == "tool" and self.block_start in msg.get("content", ""):
                msg["content"] = self.strip_blocks(msg["content"])
        return messages

    def append_to_message(self, messages: List[Dict], display: str, index: int) -> None:
        """Append *display* to the tool message at *index* in *messages*."""
        if not display or index < 0 or index >= len(messages):
            return
        if messages[index].get("role") == "tool":
            messages[index]["content"] += "\n\n" + display

    # -- full refresh cycle ------------------------------------------------

    def refresh(self, messages: List[Dict], at_index: int | None = None) -> None:
        """Full refresh: clean old blocks, regenerate, append.

        If *at_index* is given, the display is appended to that specific
        tool message (the one that triggered this tracker).  Otherwise
        it falls back to the last tool message.
        """
        self.clean_previous_blocks(messages)
        state = self.discover(messages)
        display = self.render(state)
        if display:
            if at_index is not None:
                self.append_to_message(messages, display, at_index)
            else:
                # Fallback: append to last tool message
                for i in range(len(messages) - 1, -1, -1):
                    if messages[i].get("role") == "tool":
                        self.append_to_message(messages, display, i)
                        break


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------

_registry: Dict[str, ContextTracker] = {}


def register(tracker: ContextTracker) -> None:
    """Register *tracker* so it participates in the refresh cycle."""
    _registry[tracker.name] = tracker


def get_all() -> List[ContextTracker]:
    """Return all registered trackers in insertion order."""
    return list(_registry.values())


def triggered_by(tool_name: str) -> Set[str]:
    """Return the names of trackers triggered by *tool_name*."""
    return {
        t.name for t in _registry.values()
        if tool_name in t.trigger_tools
    }
