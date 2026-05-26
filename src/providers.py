"""
Model Provider Manager

Manages multiple OpenAI-compatible API clients. All providers use the standard
OpenAI client — there is no special-casing for any provider type.
"""

import os
import logging
from typing import Dict, List, Optional, Any
import httpx
from openai import OpenAI

logger = logging.getLogger(__name__)

from .config import MODEL_PROVIDERS, DEFAULT_PROVIDER, resolve_provider as _resolve_provider


# =============================================================================
# Provider Manager
# =============================================================================

class ProviderManager:
    """Manages multiple OpenAI-compatible API clients."""

    def __init__(self):
        self._clients: Dict[str, OpenAI] = {}
        self._initialize_clients()

    def _initialize_clients(self):
        """Initialize OpenAI clients for every provider with a valid API key.

        Uses ``resolve_provider()`` from config.py (single source of truth)
        to get the canonical config for each provider."""
        from .settings_store import get_custom_providers as _gcp

        all_provider_ids = set(MODEL_PROVIDERS.keys())
        for cp in _gcp():
            cpid = cp.get("id")
            if cpid:
                all_provider_ids.add(cpid)

        for provider_id in sorted(all_provider_ids):
            try:
                resolved = _resolve_provider(provider_id)
                api_key = resolved.get("api_key", "")
                base_url = resolved.get("base_url", "")

                if not api_key or "YOUR_" in str(api_key) or not base_url:
                    continue  # Not configured — skip silently (frontend shows it anyway)

                self._clients[provider_id] = OpenAI(
                    base_url=base_url,
                    api_key=api_key,
                    timeout=httpx.Timeout(300.0, connect=30.0),
                )
                tag = "custom" if resolved.get("custom") else "built-in"
                logger.info(
                    "[ProviderManager] Initialized %s: %s (%s)",
                    tag, provider_id, resolved.get("name", provider_id),
                )
            except Exception as e:
                logger.warning(
                    "[ProviderManager] Failed to initialize %s: %s", provider_id, e
                )

    def get_client(self, provider_id: str) -> OpenAI:
        """Get the client for a specific provider.

        Raises ValueError if the provider is not configured (no API key set)
        or completely unknown.
        """
        if provider_id not in self._clients:
            # Use resolve_provider to check if it's known but unconfigured
            resolved = _resolve_provider(provider_id)
            if resolved.get("id") == provider_id:
                raise ValueError(
                    f"Provider '{provider_id}' is not configured. "
                    f"Please add an API key in Settings."
                )
            raise ValueError(f"Unknown provider: {provider_id}.")
        return self._clients[provider_id]

    def get_config(self, provider_id: str) -> dict:
        """Get the fully-resolved configuration for a provider.

        Delegates to ``resolve_provider()`` in config.py — the single source
        of truth.  Kept as a thin wrapper for backwards compatibility.
        """
        return _resolve_provider(provider_id)

    def reload(self) -> None:
        """Re-initialize all clients, picking up new/changed settings."""
        self._clients.clear()
        self._initialize_clients()
        logger.info("[ProviderManager] Reloaded — %d provider(s) available", len(self._clients))

    def list_providers(self) -> List[dict]:
        """List all built-in AND custom providers in a frontend-friendly format.

        Built-in providers are ALWAYS returned regardless of whether an API key
        has been configured yet.  Uses ``resolve_provider()`` to get the
        canonical ``api_key_configured`` flag for every provider.
        """
        result = []
        seen = set()
        # Built-in providers
        for provider_id in MODEL_PROVIDERS:
            seen.add(provider_id)
            resolved = _resolve_provider(provider_id)
            result.append({
                "id": resolved["id"],
                "name": resolved["name"],
                "description": resolved["description"],
                "supports_thinking": resolved["supports_thinking"],
                "api_key_configured": resolved["api_key_configured"],
            })
        # Custom providers (only those not shadowing a built-in)
        from .settings_store import get_custom_providers as _gcp
        for cp in _gcp():
            cpid = cp.get("id")
            if not cpid or cpid in seen:
                continue
            seen.add(cpid)
            resolved = _resolve_provider(cpid)
            result.append({
                "id": resolved["id"],
                "name": resolved["name"],
                "description": resolved["description"],
                "supports_thinking": resolved["supports_thinking"],
                "api_key_configured": resolved["api_key_configured"],
                "custom": True,
            })
        return result

    def get_default_provider(self) -> str:
        """Get the default provider ID."""
        return DEFAULT_PROVIDER


# Global singleton instance
provider_manager = ProviderManager()


# Convenience functions for external use
def get_available_providers() -> List[dict]:
    """Get list of available model providers for the frontend."""
    return provider_manager.list_providers()


def get_default_provider() -> str:
    """Get the default provider ID."""
    return provider_manager.get_default_provider()
