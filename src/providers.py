"""
Model Provider Manager

Manages multiple OpenAI-compatible API clients. All providers use the standard
OpenAI client — there is no special-casing for any provider type.
"""

import os
import logging
from typing import Dict, List, Optional, Any
from openai import OpenAI

logger = logging.getLogger(__name__)

from .config import MODEL_PROVIDERS, DEFAULT_PROVIDER


# =============================================================================
# Provider Manager
# =============================================================================

class ProviderManager:
    """Manages multiple OpenAI-compatible API clients."""

    def __init__(self):
        self._clients: Dict[str, OpenAI] = {}
        self._initialize_clients()

    def _initialize_clients(self):
        """Initialize clients for all configured providers (built-in + custom)."""
        # ── Built-in providers ─────────────────────────────────────────────
        for provider_id, config in MODEL_PROVIDERS.items():
            try:
                api_key = config.get("api_key")
                if not api_key or "YOUR_" in str(api_key):
                    # Check settings_store for a user-provided key
                    from .settings_store import get_api_key as _s_key
                    api_key = _s_key(provider_id)
                    if not api_key:
                        if config.get("id") == "gemini-3-pro-api":
                            logger.info(
                                "[ProviderManager] Skipping %s: "
                                "API key not configured (GEMINI_API_KEY or settings)",
                                provider_id,
                            )
                            continue
                        continue
                self._clients[provider_id] = OpenAI(
                    base_url=config["base_url"],
                    api_key=api_key,
                )
                logger.info("[ProviderManager] Initialized: %s (%s)", provider_id, config["name"])
            except Exception as e:
                logger.warning("[ProviderManager] Failed to initialize %s: %s", provider_id, e)

        # ── Custom providers from settings_store ───────────────────────────
        from .settings_store import get_custom_providers as _gcp
        for cp_data in _gcp():
            cpid = cp_data.get("id")
            if not cpid or cpid in self._clients:
                continue
            try:
                api_key = cp_data.get("api_key", "")
                base_url = cp_data.get("base_url", "")
                if api_key and base_url:
                    self._clients[cpid] = OpenAI(base_url=base_url, api_key=api_key)
                    logger.info("[ProviderManager] Initialized custom: %s", cp_data.get("name", cpid))
            except Exception as e:
                logger.warning("[ProviderManager] Failed to initialize custom provider %s: %s", cpid, e)

    def get_client(self, provider_id: str) -> OpenAI:
        """Get the client for a specific provider."""
        if provider_id not in self._clients:
            raise ValueError(
                f"Unknown provider: {provider_id}. Available: {list(self._clients.keys())}"
            )
        return self._clients[provider_id]

    def get_config(self, provider_id: str) -> dict:
        """Get the configuration for a specific provider, merging user overrides."""
        from .settings_store import (
            get_api_key as _gs_key,
            get_custom_providers as _gs_cp,
            get_setting_override as _gs_override,
        )
        # Check custom providers first
        for cp in _gs_cp():
            if cp["id"] == provider_id:
                return {
                    "id": cp["id"],
                    "name": cp.get("name", cp["id"]),
                    "description": cp.get("description", "Custom provider"),
                    "supports_thinking": cp.get("supports_thinking", False),
                    "base_url": cp.get("base_url", ""),
                    "api_key": cp.get("api_key", ""),
                    "model": cp.get("model", ""),
                    "custom": True,
                }
        if provider_id not in MODEL_PROVIDERS:
            raise ValueError(f"Unknown provider: {provider_id}")
        cfg = dict(MODEL_PROVIDERS[provider_id])
        # Override with settings from settings.json
        key = _gs_key(provider_id)
        if key:
            cfg["api_key"] = key
        override_url = _gs_override(provider_id, "base_url")
        if override_url:
            cfg["base_url"] = override_url
        override_model = _gs_override(provider_id, "model")
        if override_model:
            cfg["model"] = override_model
        return cfg

    def reload(self) -> None:
        """Re-initialize all clients, picking up new/changed settings."""
        self._clients.clear()
        self._initialize_clients()
        logger.info("[ProviderManager] Reloaded — %d provider(s) available", len(self._clients))

    def list_providers(self) -> List[dict]:
        """List all initialized providers in a frontend-friendly format."""
        result = [
            {
                "id": config["id"],
                "name": config["name"],
                "description": config["description"],
                "supports_thinking": config["supports_thinking"],
            }
            for provider_id, config in MODEL_PROVIDERS.items()
            if provider_id in self._clients
        ]
        # Add custom providers
        from .settings_store import get_custom_providers as _gcp
        for cp in _gcp():
            if cp.get("id") in self._clients:
                result.append({
                    "id": cp["id"],
                    "name": cp.get("name", cp["id"]),
                    "description": cp.get("description", "Custom provider"),
                    "supports_thinking": cp.get("supports_thinking", False),
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
