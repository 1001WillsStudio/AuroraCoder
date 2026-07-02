"""
Agent Memory subsystem — persistent, typed, agent-driven memory.

Lives inside ``gateway/`` because gateway is already the sole owner of
every other piece of persistent state in AuroraCoder (conversations,
settings) — see ``gateway/conversation_store.py`` and
``gateway/settings_store.py`` for the precedent this package follows.

Layers (see ``docs/code-agent-memory-design.md``):
    - Layer 1 (this package's ``schema``/``store``/``stance``/``retrieval``/
      ``redact`` modules): light, synchronous CRUD + retrieval. Exposed to
      the backend agent loop via ``/api/memory/*`` routes in
      ``gateway/routes.py``.
    - Layer 2a (``ops.extractor`` / ``ops.consolidator``): passive,
      async, structured-output-only distillation triggered at session end.
    - Layer 2b (``ops.dispatcher``): heavy, tool-using ops (gap
      investigation) — dispatched to an isolated, on-demand worker
      container. Scaffolding only; disabled by default.
"""

from .schema import MemoryItem, MEMORY_PLANES, MEMORY_TYPES
from .store import MemoryRepository, get_repository

__all__ = [
    "MemoryItem",
    "MEMORY_PLANES",
    "MEMORY_TYPES",
    "MemoryRepository",
    "get_repository",
]
