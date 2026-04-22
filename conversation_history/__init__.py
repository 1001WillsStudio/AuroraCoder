"""
Conversation History — standalone SSE proxy + conversation storage server.

Runs independently from the agent backend.  See ``api.py`` for the
FastAPI application and ``conversation_store.py`` for the storage layer.
"""

from .conversation_store import ConversationStore

__all__ = ["ConversationStore"]
