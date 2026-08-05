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
from typing import Dict

import httpx
from openai import OpenAI

from .config import MODEL_PROVIDERS, DEFAULT_PROVIDER, PROVIDER_DEFAULT_MODELS, DATA_DIR

logger = logging.getLogger(__name__)


# Use the SAME data dir as config.py / settings_store (single source of truth)
# so the chat path reads the same settings.json keyed by AURORACODER_DOCKER /
# AURORACODER_DATA_DIR — never a split-brain /app/data default.
SETTINGS_PATH = DATA_DIR / "settings.json"


def _load_settings() -> dict:
    """Read settings.json from disk (shared volume).  Returns {} on any error."""
    try:
        if SETTINGS_PATH.exists():
            return json.loads(SETTINGS_PATH.read_text())
    except Exception:
        pass
    return {}


def _get_custom_providers() -> list:
    """Read custom providers from settings.json."""
    return _load_settings().get("custom_providers", [])


def _get_custom_provider(provider_id: str) -> dict | None:
    """Return the custom provider dict for *provider_id*, or None."""
    for cp in _get_custom_providers():
        if cp.get("id") == provider_id:
            return cp
    return None


def _resolve_api_key(provider_id: str, default_val: str) -> str:
    """Resolve an API key: custom_providers → settings.json api_keys → default."""
    # 1) Check custom_providers first
    cp = _get_custom_provider(provider_id)
    if cp:
        key = cp.get("api_key", "")
        if key and key is not True and "YOUR_" not in str(key):
            return key
    # 2) Check settings.json api_keys
    settings = _load_settings()
    key = settings.get("api_keys", {}).get(provider_id, "")
    if key and key is not True and "YOUR_" not in str(key):
        return key
    # 3) Fall back to default
    if default_val and "YOUR_" not in str(default_val):
        return default_val
    return ""


def _resolve_base_url(provider_id: str, default_val: str) -> str:
    """Resolve base_url: custom_providers → settings override → default."""
    # 1) Check custom_providers first
    cp = _get_custom_provider(provider_id)
    if cp and cp.get("base_url", "").strip():
        return cp["base_url"].strip()
    # 2) Check provider_overrides
    settings = _load_settings()
    override = settings.get("provider_overrides", {}).get(provider_id, {}).get("base_url", "")
    return override or default_val


def _resolve_model(provider_id: str, default_val: str) -> str:
    """Resolve model: custom_providers → provider_overrides → provider_models → PROVIDER_DEFAULT_MODELS → default."""
    # 1) Check custom_providers
    cp = _get_custom_provider(provider_id)
    if cp and cp.get("model", "").strip():
        return cp["model"].strip()
    # 2) Check provider_overrides
    settings = _load_settings()
    override = settings.get("provider_overrides", {}).get(provider_id, {}).get("model", "")
    if override:
        return override
    # 3) Check provider_models — first enabled model
    pm = settings.get("provider_models", {}).get(provider_id, [])
    if pm:
        first = pm[0]
        return first["id"] if isinstance(first, dict) else first
    # 4) Check PROVIDER_DEFAULT_MODELS
    defaults = PROVIDER_DEFAULT_MODELS.get(provider_id, [])
    if defaults:
        return defaults[0]["id"]
    return default_val


def _coerce_model_selection(value) -> tuple:
    """Coerce a model-selection value to ``(provider_id, model_id)``.

    Readers prefer the structured ``{"provider", "model"}`` form (the
    canonical on-disk shape written by the settings store).  This shim also
    absorbs legacy representations so the chat path never breaks on a
    settings.json that has not yet been migrated:
        • dict    → ("provider", "model")
        • "a::b"  → ("a", "b")
        • "a"     → ("a", "")
    """
    if isinstance(value, dict):
        return (value.get("provider") or "", value.get("model") or "")
    if isinstance(value, str) and value.strip():
        s = value.strip()
        if "::" in s:
            p, m = s.split("::", 1)
            return (p.strip(), m.strip())
        return (s, "")
    return ("", "")


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
                # Look up custom provider first — its data overrides built-in defaults
                custom = _get_custom_provider(provider_id)
                if custom:
                    provider_info = dict(custom)
                    default = {
                        "api_key": custom.get("api_key", ""),
                        "base_url": custom.get("base_url", ""),
                        "model": custom.get("model", ""),
                        "name": custom.get("name", provider_id),
                    }
                else:
                    provider_info = MODEL_PROVIDERS.get(provider_id, {})
                    default = dict(provider_info)

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
        # Structured selection: {"provider": ..., "model": ...}.  ``model`` ("")
        # ⇒ auto-select the provider's first enabled model (matching the agent
        # default).  _coerce_model_selection absorbs any legacy on-disk form.
        ws = other.get("web_secondary", {})
        provider_id, model_id = _coerce_model_selection(ws.get("model"))

        if provider_id:
            # Resolve provider config for the secondary model
            custom = _get_custom_provider(provider_id)
            if custom:
                default = dict(custom)
            else:
                default = MODEL_PROVIDERS.get(provider_id, MODEL_PROVIDERS.get(DEFAULT_PROVIDER, {}))
            base_url = _resolve_base_url(provider_id, default.get("base_url", ""))
            api_key = _resolve_api_key(provider_id, default.get("api_key", ""))
            model = model_id or _resolve_model(provider_id, "")
        else:
            # No provider selected — use defaults from env or config
            default_prov = MODEL_PROVIDERS.get(DEFAULT_PROVIDER, {})
            base_url = default_prov.get("base_url", "")
            api_key = os.environ.get("DEEPSEEK_API_KEY", default_prov.get("api_key", ""))
            model = _resolve_model(DEFAULT_PROVIDER, "")

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

        edit_mode = agent.get("edit_mode")
        if edit_mode and edit_mode in ("aurora", "normal"):
            os.environ["EDIT_MODE"] = edit_mode

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
        For custom providers, the custom-provider entry serves as the default
        instead of falling back to the built-in DEFAULT_PROVIDER.
        """
        custom = _get_custom_provider(provider_id)
        if custom:
            default = {
                k: v
                for k, v in custom.items()
                if k != "id"
            }
            default["provider_id"] = provider_id
        else:
            default = MODEL_PROVIDERS.get(
                provider_id, MODEL_PROVIDERS.get(DEFAULT_PROVIDER, {})
            )
        resolved = dict(default)
        resolved["api_key"] = _resolve_api_key(provider_id, default.get("api_key", ""))
        resolved["base_url"] = _resolve_base_url(provider_id, default.get("base_url", ""))
        resolved["model"] = _resolve_model(provider_id, default.get("model", ""))
        return resolved


# Global singleton
provider_manager = ProviderManager()
