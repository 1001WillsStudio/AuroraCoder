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


DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
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
        """Re-read settings.json, resolve all providers, rebuild client cache,
        and sync non-provider tool settings (Google Search, Web Secondary
        Model, max_tool_concurrency, terminal_max_output) into the backend
        process's environment variables.

        Called at startup and when the gateway POSTs to ``/api/reload``.
        """
        self._reload_provider_clients()
        self._sync_tool_env_vars()
        # Let tool_executor recreate its thread pool on next use
        try:
            from .tool_executor import reload_concurrency  # noqa: F811
            reload_concurrency()
        except ImportError:
            pass

    def _reload_provider_clients(self) -> None:
        """Rebuild OpenAI client cache from settings.json."""
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

    def _sync_tool_env_vars(self) -> None:
        """Push non-provider tool settings from settings.json into environment
        variables for the backend process.

        This mirrors ``gateway.provider_registry.sync_tool_env_vars``,
        but runs **inside the backend process** so that tools like
        ``google_search`` and ``web_browser`` actually see the updated values.

        The gateway's version only affects the gateway process — by also
        calling this from ``reload()`` (which the gateway triggers via
        ``POST /api/reload``), the backend's env vars stay in sync.
        """
        settings = _load_settings()
        other = settings.get("other", {})
        api_keys = settings.get("api_keys", {})

        # ── Google Search ──
        gs_api_key = api_keys.get("google_search", "")
        if gs_api_key and gs_api_key is not True and "YOUR_" not in str(gs_api_key):
            os.environ["GOOGLE_SEARCH_API_KEY"] = gs_api_key
        else:
            # Fall back to env var (don't clear if already set from .env)
            gs_api_key = os.environ.get("GOOGLE_SEARCH_API_KEY", "")

        gs = other.get("google_search", {})
        cse_id = gs.get("cse_id", "")
        if cse_id:
            os.environ["GOOGLE_CSE_ID"] = cse_id
        else:
            cse_id = os.environ.get("GOOGLE_CSE_ID", "")

        # ── Web Secondary Model ──
        ws = other.get("web_secondary", {})
        provider_id = ws.get("provider", "")

        if provider_id:
            # Resolve provider config for the secondary model
            default = MODEL_PROVIDERS.get(provider_id, MODEL_PROVIDERS.get(DEFAULT_PROVIDER, {}))
            base_url = _resolve_base_url(provider_id, default.get("base_url", ""))
            api_key = _resolve_api_key(provider_id, default.get("api_key", ""))
            model = ws.get("model", "") or _resolve_model(provider_id, default.get("model", ""))
        else:
            # No provider selected — use defaults from env or config
            default_prov = MODEL_PROVIDERS.get(DEFAULT_PROVIDER, {})
            base_url = default_prov.get("base_url", "")
            api_key = os.environ.get("DEEPSEEK_API_KEY", default_prov.get("api_key", ""))
            model = default_prov.get("model", "")

        if base_url:
            os.environ["WEB_SECONDARY_BASE_URL"] = base_url
        if api_key:
            os.environ["WEB_SECONDARY_API_KEY"] = api_key
        os.environ["WEB_SECONDARY_MODEL"] = model or ""
        os.environ["WEB_SECONDARY_MAX_TOKENS"] = str(
            ws.get(
                "max_tokens",
                int(os.environ.get("WEB_SECONDARY_MODEL_MAX_TOKENS", "4096")),
            )
        )

        # ── Agent / Tool behaviour ──
        agent = other.get("agent", {})

        mtc = agent.get("max_tool_concurrency")
        if mtc is not None and str(mtc).isdigit():
            os.environ["MAX_TOOL_CONCURRENCY"] = str(mtc)

        tmo = agent.get("terminal_max_output")
        if tmo is not None and str(tmo).isdigit():
            os.environ["TERMINAL_MAX_OUTPUT_CHARS"] = str(tmo)

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
