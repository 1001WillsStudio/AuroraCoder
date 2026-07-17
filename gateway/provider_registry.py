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

from src.config import MODEL_PROVIDERS, DEFAULT_PROVIDER, PROVIDER_DESCRIPTIONS, PROVIDER_DEFAULT_MODELS
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
        extra_body, context_window, custom,
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
            prov = dict(MODEL_PROVIDERS[DEFAULT_PROVIDER])
        else:
            prov = dict(match)
            custom = True

    # ── api_key: settings.json → env var → MODEL_PROVIDERS default ──
    resolved_key = get_api_key(provider_id)
    # Reject non-string values (boolean True is a sentinel, not a real key)
    if isinstance(resolved_key, str) and resolved_key and "YOUR_" not in resolved_key:
        prov["api_key"] = resolved_key
    elif not isinstance(prov.get("api_key"), str) or not prov["api_key"]:
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
    prov.setdefault("description",
        PROVIDER_DESCRIPTIONS.get(provider_id, "") if not custom else "Custom provider")
    prov.setdefault("extra_body", None)
    prov.setdefault("context_window", 128_000)

    return prov


def get_default_model_entry() -> str:
    """Return the id of the default *model* entry shown in the sidebar.

    The agent's default is a *model selection* stored structurally as
    ``other.agent.default_model = {"provider": ..., "model": ...}`` (the
    settings store normalizes any legacy form to this on read).  We map that
    to a single entry id from ``get_available_providers()`` (a composite
    ``provider::model_id`` when the provider has enabled models, else a bare
    family id) so the frontend can use it as a scalar selection.
    """
    settings = get_all_settings()
    avail = get_available_providers()
    if not avail:
        return DEFAULT_PROVIDER

    agent = settings.get("other", {}).get("agent", {})
    dm = agent.get("default_model")
    if isinstance(dm, dict):
        provider_id = dm.get("provider", "")
        model_id = dm.get("model", "")
        if provider_id:
            if model_id:
                for e in avail:
                    if e.get("provider_id") == provider_id and e.get("model") == model_id:
                        return e["id"]
            m = next((e for e in avail if e.get("provider_id") == provider_id), None)
            if m:
                return m["id"]

    # System default: first entry belonging to DEFAULT_PROVIDER, else first
    m = next((e for e in avail if e.get("provider_id") == DEFAULT_PROVIDER), None)
    return m["id"] if m else avail[0]["id"]


def get_available_providers() -> List[dict]:
    """Return a frontend-friendly list of all providers (built-in + custom)."""
    result = []
    seen: set = set()

    for provider_id in MODEL_PROVIDERS:
        seen.add(provider_id)
        r = resolve_provider(provider_id)
        settings = get_all_settings()
        pm = settings.get("provider_models", {}).get(provider_id, [])

        if pm:
            for m in pm:
                mid = m["id"] if isinstance(m, dict) else m
                result.append({
                    "id": f"{provider_id}::{mid}",
                    "name": f"{r['name']} / {mid}",
                    "api_key_configured": r["api_key_configured"],
                    "provider_id": provider_id,
                    "model": mid,
                })
        # If no user-selected models, show the provider as a single entry
        # so the sidebar has something to click — chat will use default model.
        else:
            result.append({
                "id": provider_id,
                "name": r["name"],
                "api_key_configured": r["api_key_configured"],
                "provider_id": provider_id,
            })

    for cp in get_custom_providers():
        cpid = cp.get("id")
        if not cpid or cpid in seen:
            continue
        seen.add(cpid)
        r = resolve_provider(cpid)
        settings = get_all_settings()
        pm = settings.get("provider_models", {}).get(cpid, [])

        if pm:
            for m in pm:
                mid = m["id"] if isinstance(m, dict) else m
                result.append({
                    "id": f"{cpid}::{mid}",
                    "name": f"{r['name']} / {mid}",
                    "api_key_configured": r["api_key_configured"],
                    "provider_id": cpid,
                    "model": mid,
                    "custom": True,
                })
        else:
            result.append({
                "id": r["id"],
                "name": r["name"],
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
    """Resolve the web secondary model configuration.

    Reads the structured selection
    ``other.web_secondary.model = {"provider": ..., "model": ...}``
    (normalized on read by the settings store).  ``model`` ("") ⇒ auto-select
    the provider's first enabled model (else its default model).  Returns
    ``{provider_id, base_url, api_key, model}`` ready for the OpenAI client.
    """
    settings = get_all_settings()
    ws = settings.get("other", {}).get("web_secondary", {})

    sel = ws.get("model")
    provider_id = ""
    model_id = ""
    if isinstance(sel, dict):
        provider_id = sel.get("provider", "")
        model_id = sel.get("model", "")

    if provider_id:
        r = resolve_provider(provider_id)
        if not model_id:
            # Auto-select: first enabled model, else the provider's default model
            pm = settings.get("provider_models", {}).get(provider_id, [])
            if pm:
                first = pm[0]
                model_id = first["id"] if isinstance(first, dict) else first
            elif PROVIDER_DEFAULT_MODELS.get(provider_id):
                model_id = PROVIDER_DEFAULT_MODELS[provider_id][0]["id"]
        return {
            "provider_id": provider_id,
            "base_url": r["base_url"],
            "api_key": r["api_key"],
            "model": model_id,
        }
    return {"provider_id": "", "base_url": "", "api_key": "", "model": ""}


def get_memory_extraction_config() -> dict:
    """Resolve the model used for passive memory extraction/consolidation
    (Layer 2a). Falls back to the agent's own default provider/model — this
    is a structured-output-only, no-tool call, so it doesn't need a
    particularly strong model, but reuses the default rather than
    inventing a second required setting for v1.

    ``extraction_provider`` is deliberately kept as a single provider
    *family* id (e.g. "deepseek"/"opencode"/"nvidia" — never a legacy
    per-variant id like "opencode-ds-v4-pro", which no longer resolves to
    anything meaningful under the family+model scheme in src/config.py) so
    this setting doesn't also need its own model picker; it always uses
    that family's default model. If unset, falls through to whatever the
    agent's own default_model is currently pointed at (same
    ``{"provider": ..., "model": ...}`` shape everything else uses), and
    finally to PROVIDER_DEFAULT_MODELS if even that has no model pinned.
    """
    settings = get_all_settings()
    mem = settings.get("other", {}).get("memory", {})
    provider_id = mem.get("extraction_provider", "")
    model_id = ""
    if not provider_id:
        default_model = settings.get("other", {}).get("agent", {}).get("default_model")
        if isinstance(default_model, dict):
            provider_id = default_model.get("provider", "")
            model_id = default_model.get("model", "")
    provider_id = provider_id or DEFAULT_PROVIDER

    r = resolve_provider(provider_id)
    if not model_id:
        model_id = r.get("model") or ""
    if not model_id:
        defaults = PROVIDER_DEFAULT_MODELS.get(provider_id, [])
        model_id = defaults[0]["id"] if defaults else ""

    return {
        "provider_id": provider_id,
        "base_url": r["base_url"],
        "api_key": r["api_key"],
        "model": model_id,
    }


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

def sync_tool_env_vars():
    """Push non-provider settings into environment for src-side tool access.

    Since ``src/`` cannot import from ``gateway/``, tools like Google Search
    and Web Browser read their config from environment variables.  This
    function keeps those env vars in sync with settings.json.

    Called by the gateway on startup and after every settings save.
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
