"""
Provider Registry — the "brain" of provider management.

Resolves provider configurations by merging static definitions
(``src.config.MODEL_PROVIDERS``) with user settings from ``gateway.settings_store``.
Also handles creating OpenAI clients and pushing them into the thin client
cache inside ``src.providers``.

This is the **only** module that imports from both ``src.config`` and
``gateway.settings_store``, keeping the dependency direction one-way:
``gateway → src``.
"""

import logging
import os
from typing import List

import httpx
from openai import OpenAI

from src.config import MODEL_PROVIDERS, DEFAULT_PROVIDER as _STATIC_DEFAULT
from gateway.settings_store import (
    get_api_key,
    get_custom_providers,
    get_other_settings,
    get_setting_override,
    get_all_settings,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Resolution
# ═══════════════════════════════════════════════════════════════════════════

def resolve_provider(provider_id: str) -> dict:
    """Resolve a provider ID to its **full canonical config**.

    Priority chain:
        1. settings.json  (api_keys, provider_overrides.base_url/model)
        2. Environment variables
        3. MODEL_PROVIDERS defaults (src/config.py)

    Returns a dict with **every** field consumers need:
        id, name, description, base_url, api_key, model,
        supports_thinking, extra_body, context_window, custom,
        api_key_configured

    For unknown provider IDs the default provider is returned as a fallback.
    """
    custom = False
    if provider_id in MODEL_PROVIDERS:
        prov = dict(MODEL_PROVIDERS[provider_id])
    else:
        custom_list = get_custom_providers()
        match = next((cp for cp in custom_list if cp.get("id") == provider_id), None)
        if match is None:
            # Unknown — fall back to default provider
            prov = dict(MODEL_PROVIDERS[_STATIC_DEFAULT])
        else:
            prov = dict(match)
            custom = True

    # ── api_key: settings.json → env var → MODEL_PROVIDERS default ──
    resolved_key = get_api_key(provider_id)
    if resolved_key:
        prov["api_key"] = resolved_key
    elif not prov.get("api_key"):
        prov["api_key"] = ""

    # ── Per-provider overrides (base_url, model) ──
    override_base = get_setting_override(provider_id, "base_url")
    if override_base:
        prov["base_url"] = override_base
    override_model = get_setting_override(provider_id, "model")
    if override_model:
        prov["model"] = override_model

    # ── Derived fields ──
    key = prov.get("api_key", "")
    prov["custom"] = custom
    prov["api_key_configured"] = bool(key and "YOUR_" not in str(key))

    # Ensure all expected keys exist
    prov.setdefault("name", prov.get("id", provider_id))
    prov.setdefault("description", "Custom provider" if custom else "")
    prov.setdefault("supports_thinking", True)
    prov.setdefault("extra_body", None)
    prov.setdefault("context_window", 128_000)

    return prov


def get_default_provider() -> str:
    """Return the settings-aware default provider ID."""
    settings = get_all_settings()
    return settings.get("other", {}).get("agent", {}).get(
        "default_provider", _STATIC_DEFAULT
    )


def get_available_providers() -> List[dict]:
    """Return a frontend-friendly list of all providers (built-in + custom)."""
    result = []
    seen: set = set()

    for provider_id in MODEL_PROVIDERS:
        seen.add(provider_id)
        r = resolve_provider(provider_id)
        result.append({
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "supports_thinking": r["supports_thinking"],
            "api_key_configured": r["api_key_configured"],
        })

    for cp in get_custom_providers():
        cpid = cp.get("id")
        if not cpid or cpid in seen:
            continue
        seen.add(cpid)
        r = resolve_provider(cpid)
        result.append({
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "supports_thinking": r["supports_thinking"],
            "api_key_configured": r["api_key_configured"],
            "custom": True,
        })

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Dynamic settings (formerly in src/config.py)
# ═══════════════════════════════════════════════════════════════════════════

def get_max_iterations() -> int:
    """Return the maximum number of agent iterations."""
    settings = get_all_settings()
    return settings.get("other", {}).get("agent", {}).get(
        "max_iterations",
        int(os.environ.get("MAX_ITERATIONS", "30")),
    )


def get_max_concurrent_tools() -> int:
    """Return the maximum number of concurrent tool calls."""
    settings = get_all_settings()
    return settings.get("other", {}).get("agent", {}).get(
        "max_concurrent_tools",
        int(os.environ.get("MAX_CONCURRENT_TOOLS", "8")),
    )


def get_web_secondary_config() -> dict:
    """Resolve the web secondary model configuration."""
    settings = get_all_settings()
    ws = settings.get("other", {}).get("web_secondary", {})
    provider_id = ws.get("provider", "")

    if provider_id:
        r = resolve_provider(provider_id)
        return {
            "provider_id": provider_id,
            "base_url": r["base_url"],
            "api_key": r["api_key"],
            "model": ws.get("model", "") or r.get("model", ""),
        }
    return {"provider_id": "", "base_url": "", "api_key": "", "model": ""}


def get_toolstore_url() -> str:
    """Return the ToolStore URL from settings or env."""
    settings = get_all_settings()
    return settings.get("other", {}).get("toolstore", {}).get(
        "url",
        os.environ.get("TOOLSTORE_URL", "http://localhost:8765"),
    )


def get_toolstore_token() -> str:
    """Return the ToolStore auth token from settings or env."""
    settings = get_all_settings()
    return settings.get("other", {}).get("toolstore", {}).get(
        "token",
        os.environ.get("TOOLSTORE_TOKEN", ""),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Client sync — push resolved clients into src.providers
# ═══════════════════════════════════════════════════════════════════════════

def sync_clients_to_src():
    """Resolve all configured providers, create OpenAI clients, push to src.

    Called by the gateway after settings are saved and on startup.
    Also updates environment variables for src-side tools (e.g. Google Search).
    """
    from src.providers import provider_manager

    provider_manager.clear()

    all_ids = set(MODEL_PROVIDERS.keys())
    for cp in get_custom_providers():
        cpid = cp.get("id")
        if cpid:
            all_ids.add(cpid)

    for provider_id in sorted(all_ids):
        try:
            r = resolve_provider(provider_id)
            api_key = r.get("api_key", "")
            base_url = r.get("base_url", "")
            if not api_key or "YOUR_" in str(api_key) or not base_url:
                continue

            client = OpenAI(
                base_url=base_url,
                api_key=api_key,
                timeout=httpx.Timeout(300.0, connect=30.0),
            )
            provider_manager.set_client(provider_id, client)
            tag = "custom" if r.get("custom") else "built-in"
            logger.info(
                "[ProviderRegistry] Synced %s client: %s (%s)",
                tag, provider_id, r.get("name", provider_id),
            )
        except Exception as e:
            logger.warning(
                "[ProviderRegistry] Failed to sync %s: %s", provider_id, e
            )

    # ── Also sync env vars for src-side tools that don't use providers ──
    _sync_tool_env_vars()


def _sync_tool_env_vars():
    """Push non-provider settings into environment for src-side tool access.

    Since ``src/`` cannot import from ``gateway/``, tools like Google Search
    and Web Browser read their config from environment variables.  This
    function keeps those env vars in sync with settings.json.
    """
    # ── Google Search ──
    api_key = get_api_key("google_search")
    if api_key:
        os.environ["GOOGLE_SEARCH_API_KEY"] = api_key

    other = get_other_settings()
    gs = other.get("google_search", {})
    cse_id = gs.get("cse_id", "")
    if cse_id:
        os.environ["GOOGLE_CSE_ID"] = cse_id

    # ── Web Secondary Model ──
    wsc = get_web_secondary_config()
    if wsc.get("base_url"):
        os.environ["WEB_SECONDARY_BASE_URL"] = wsc["base_url"]
    if wsc.get("api_key"):
        os.environ["WEB_SECONDARY_API_KEY"] = wsc["api_key"]
    os.environ["WEB_SECONDARY_MODEL"] = wsc.get("model", "")
    os.environ["WEB_SECONDARY_MAX_TOKENS"] = str(
        other.get("web_secondary", {}).get(
            "max_tokens",
            int(os.environ.get("WEB_SECONDARY_MODEL_MAX_TOKENS", "4096")),
        )
    )
