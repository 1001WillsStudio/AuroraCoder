"""
Agent Memory subsystem — persistent, typed, agent-driven memory.

A top-level package, not nested under ``gateway/`` — it's a distinct
subsystem that happens to run inside the same process as the gateway
(exposed via ``/api/memory/*`` routes in ``gateway/routes.py``, same as
``gateway/conversation_store.py`` and ``gateway/settings_store.py`` are
the gateway's own persistence — memory reuses that "gateway owns all
persistent state" precedent for where it *runs*, without being one of
the gateway's own concerns organizationally).

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
