"""
Conversation Gateway — middleware between frontend and agent backend.

Handles SSE proxying, conversation persistence, stream management,
workspace file display, and all "dirty work" between the UI and the
stateless agent loop.  See ``api.py`` for the FastAPI application,
``conversation_store.py`` for the storage layer, and ``workspace.py``
for file-display utilities.
"""

from .conversation_store import ConversationStore

__all__ = ["ConversationStore"]
