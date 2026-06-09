"""
Panel — the general form of the "Living Tool State" pattern.

Each Panel subclass:
  1. Scans message history to discover current state ("what's open?")
  2. Renders a display block with a markdown heading
  3. Manages its own ``role: "system"`` message — finds, removes, and
     replaces itself by content prefix, no custom dict keys
  4. Registers trigger tools that activate it

To add a new living-state panel (browser tabs, API connections, etc.)
just subclass ``Panel``, set ``heading`` / ``trigger_tools``, implement
``discover()`` and ``render()``, then ``register()`` it — zero changes
to ``main_flow.py`` or ``tool_executor.py``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Set


class Panel(ABC):
    """A self-managing system-message panel driven by discovered state.

    Subclasses must provide:

    * ``name`` — unique identifier for this panel
    * ``heading`` — the markdown heading that identifies the panel's
      system message (e.g. ``"# Panel - Code Interpreter"``)
    * ``trigger_tools`` — set of tool names that activate it
    * ``discover(messages)`` — scan history, return current state
    * ``render(state)`` — produce the display content (must start
      with ``heading``)
    """

    # -- override these ---------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def heading(self) -> str: ...

    @property
    @abstractmethod
    def trigger_tools(self) -> Set[str]: ...

    @property
    def block_end(self) -> str:
        """Closing marker — always empty for panels (the message boundary
        is the scope).  Kept for compatibility with older tooling."""
        return ""

    @abstractmethod
    def discover(self, messages: List[Dict]) -> object:
        """Scan *messages* and return the current state.

        The return type is panel-specific (e.g. ``Set[str]`` of open
        files).  It only needs to be understood by ``render()``.
        """
        ...

    @abstractmethod
    def render(self, state: object) -> str:
        """Produce the display for *state*.

        Must start with ``heading``.  Return an empty string when
        there is nothing to show.
        """
        ...

    # -- message management -----------------------------------------------

    def find(self, messages: List[Dict]) -> int | None:
        """Return the index of this panel's message in *messages*, or ``None``."""
        for i, m in enumerate(messages):
            if m.get("role") == "system" and (m.get("content") or "").startswith(self.heading):
                return i
        return None

    def remove(self, messages: List[Dict]) -> None:
        """Remove every occurrence of this panel from *messages* (in-place)."""
        messages[:] = [
            m for m in messages
            if not (
                m.get("role") == "system"
                and (m.get("content") or "").startswith(self.heading)
            )
        ]

    def update(self, messages: List[Dict], content: str) -> None:
        """Replace the previous panel (if any) with *content*.

        If *content* is empty the old panel is removed and nothing is
        appended — equivalent to closing the panel.
        """
        self.remove(messages)
        if content:
            messages.append({"role": "system", "content": content})

    # -- block helpers (rarely overridden) ---------------------------------

    def strip_blocks(self, content: str) -> str:
        """Remove all instances of this panel's block from *content*."""
        import re

        if not content:
            return content
        if self.block_end:
            pattern = re.compile(
                re.escape(self.heading) + r'.*?' + re.escape(self.block_end),
                re.DOTALL,
            )
        else:
            pattern = re.compile(
                re.escape(self.heading) + r'.*$',
                re.DOTALL,
            )
        cleaned = pattern.sub('', content)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()

    def clean_previous_blocks(self, messages: List[Dict]) -> List[Dict]:
        """Strip stale display blocks from non-panel system messages (in-place).

        Panel messages (content starts with ``heading``) are skipped —
        they are managed by :meth:`refresh`.
        """
        for msg in messages:
            if msg.get("role") != "system":
                continue
            content = msg.get("content") or ""
            if self.heading not in content:
                continue
            if content.startswith(self.heading):
                continue  # legitimate panel — managed by refresh()
            msg["content"] = self.strip_blocks(content)
        return messages

    def append_to_message(self, messages: List[Dict], display: str, index: int) -> None:
        """Append *display* to the tool message at *index* in *messages*."""
        if not display or index < 0 or index >= len(messages):
            return
        if messages[index].get("role") == "tool":
            messages[index]["content"] += "\n\n" + display

    # -- full refresh cycle ------------------------------------------------

    def refresh(self, messages: List[Dict], at_index: int | None = None) -> None:
        """Full refresh: regenerate the panel as a system message.

        Discovers current state, renders it, then calls :meth:`update`
        which removes the old panel (by heading prefix) and appends a
        fresh one at the tail.

        *at_index* is kept for API compatibility but is no longer used.
        """
        state = self.discover(messages)
        display = self.render(state)
        self.update(messages, display)


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------

_registry: Dict[str, Panel] = {}


def register(panel: Panel) -> None:
    """Register *panel* so it participates in the refresh cycle."""
    _registry[panel.name] = panel


def get_all() -> List[Panel]:
    """Return all registered panels in insertion order."""
    return list(_registry.values())


def triggered_by(tool_name: str) -> Set[str]:
    """Return the names of panels triggered by *tool_name*."""
    return {
        p.name for p in _registry.values()
        if tool_name in p.trigger_tools
    }
