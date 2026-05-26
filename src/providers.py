"""
Model Provider Manager — self-contained client cache.

Holds OpenAI client objects for the agent loop.  On ``reload()``, reads
``settings.json`` directly from disk (shared Docker volume) and merges
with ``MODEL_PROVIDERS`` static config — **no** gateway imports.

The gateway triggers reload by POSTing to the backend's ``/api/reload``
endpoint after settings changes.
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict

import httpx
from openai import OpenAI

from .config import MODEL_PROVIDERS, DEFAULT_PROVIDER

logger = logging.getLogger(__name__)


def _is_docker() -> bool:
    """Best-effort Docker detection."""
    return os.path.exists("/.dockerenv") or "docker" in (os.environ.get("container", "") or "")


def _data_dir() -> Path:
    if _is_docker():
        return Path("/app/data")
    return Path(
        os.environ.get(
            "THINKTOOL_DATA_DIR",
            os.environ.get("DATA_DIR", os.path.expanduser("~/.thinktool/data")),
        )
    )


DATA_DIR = _data_dir()
SETTINGS_PATH = DATA_DIR / "settings.json"


def _load_settings() -> dict:
    """Read settings.json from disk (shared volume).  Returns {} on any error."""
    try:
        if SETTINGS_PATH.exists():
            return json.loads(SETTINGS_PATH.read_text())
    except Exception:
        pass
    return {}


def _resolve_api_key(provider_id: str, default_val: str) -> str:
    """Resolve an API key: settings.json → env var → default."""
    settings = _load_settings()
    key = settings.get("api_keys", {}).get(provider_id, "")
    if key and key is not True and "YOUR_" not in str(key):
        return key
    if default_val and "YOUR_" not in str(default_val):
        return default_val
    return ""


def _resolve_base_url(provider_id: str, default_val: str) -> str:
    """Resolve base_url: settings override → default."""
    settings = _load_settings()
    override = settings.get("provider_overrides", {}).get(provider_id, {}).get("base_url", "")
    return override or default_val


def _resolve_model(provider_id: str, default_val: str) -> str:
    """Resolve model: settings override → default."""
    settings = _load_settings()
    override = settings.get("provider_overrides", {}).get(provider_id, {}).get("model", "")
    return override or default_val


def _get_custom_providers() -> list:
    """Read custom providers from settings.json."""
    return _load_settings().get("custom_providers", [])


# =============================================================================
# Provider Manager
# =============================================================================


class ProviderManager:
    """Self-contained cache of OpenAI clients.  ``reload()`` reads disk directly."""

    def __init__(self):
        self._clients: Dict[str, OpenAI] = {}

    # ── Reload (self-contained, no gateway imports) ──────────────────────

    def reload(self) -> None:
        """Re-read settings.json, resolve all providers, rebuild client cache.

        Called at startup and when the gateway POSTs to ``/api/reload``.
        """
        self._clients.clear()
        all_ids = list(MODEL_PROVIDERS.keys())

        for cp in _get_custom_providers():
            cpid = cp.get("id")
            if cpid and cpid not in all_ids:
                all_ids.append(cpid)

        for provider_id in all_ids:
            try:
                default = MODEL_PROVIDERS.get(provider_id, {})
                api_key = _resolve_api_key(provider_id, default.get("api_key", ""))
                base_url = _resolve_base_url(provider_id, default.get("base_url", ""))
                if not api_key or "YOUR_" in str(api_key) or not base_url:
                    continue
                self._clients[provider_id] = OpenAI(
                    base_url=base_url,
                    api_key=api_key,
                    timeout=httpx.Timeout(300.0, connect=30.0),
                )
                logger.info(
                    "[ProviderManager] Loaded client: %s (%s)",
                    provider_id, default.get("name", provider_id),
                )
            except Exception as e:
                logger.warning(
                    "[ProviderManager] Failed to load %s: %s", provider_id, e
                )

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

    # ── Config (static + settings.json, for main_flow) ──────────────────

    def get_config(self, provider_id: str) -> dict:
        """Return resolved provider config for *main_flow*.

        Resolves from settings.json + MODEL_PROVIDERS.
        """
        default = MODEL_PROVIDERS.get(provider_id, MODEL_PROVIDERS.get(DEFAULT_PROVIDER, {}))
        resolved = dict(default)
        resolved["api_key"] = _resolve_api_key(provider_id, default.get("api_key", ""))
        resolved["base_url"] = _resolve_base_url(provider_id, default.get("base_url", ""))
        resolved["model"] = _resolve_model(provider_id, default.get("model", ""))
        return resolved


# Global singleton
provider_manager = ProviderManager()
