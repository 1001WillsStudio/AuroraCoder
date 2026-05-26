"""
Model Provider Manager — thin client cache.

Holds OpenAI client objects that the agent loop needs at runtime.
Does NOT resolve providers, does NOT import settings_store, does
NOT know about persistence.  The gateway (``gateway.provider_registry``)
pushes clients in via ``set_client()`` after settings changes.

This keeps the dependency direction one-way: gateway → src.
"""

import logging
from typing import Dict, Optional

from openai import OpenAI
from .config import MODEL_PROVIDERS, DEFAULT_PROVIDER

logger = logging.getLogger(__name__)

# =============================================================================
# Provider Manager — dumb cache
# =============================================================================


class ProviderManager:
    """Dumb cache of OpenAI clients.  The gateway owns all intelligence."""

    def __init__(self):
        self._clients: Dict[str, OpenAI] = {}

    # ── Gateway API (push) ──────────────────────────────────────────────

    def set_client(self, provider_id: str, client: OpenAI) -> None:
        """Store a ready-to-use OpenAI client.  Called by the gateway."""
        self._clients[provider_id] = client

    def clear(self) -> None:
        """Remove all cached clients.  Called by the gateway before a full sync."""
        self._clients.clear()

    # ── Agent API (pull) ────────────────────────────────────────────────

    def get_client(self, provider_id: str) -> OpenAI:
        """Return the cached client, or raise if not configured."""
        if provider_id not in self._clients:
            raise ValueError(
                f"Provider '{provider_id}' is not configured. "
                f"Please add an API key in Settings."
            )
        return self._clients[provider_id]

    def has_client(self, provider_id: str) -> bool:
        """Check whether a client is cached for *provider_id*."""
        return provider_id in self._clients

    # ── Config (static, for main_flow) ──────────────────────────────────

    def get_config(self, provider_id: str) -> dict:
        """Return the static provider config for *main_flow*.

        Reads from ``MODEL_PROVIDERS`` (env vars + built-in defaults).
        Does NOT consult ``settings.json`` — that's the gateway's job.
        """
        if provider_id in MODEL_PROVIDERS:
            return dict(MODEL_PROVIDERS[provider_id])
        return dict(MODEL_PROVIDERS[DEFAULT_PROVIDER])


# Global singleton — the gateway pushes clients into this at startup and
# after every settings save.
provider_manager = ProviderManager()
