"""
Persistent user settings store.

Reads/writes a JSON file in the data directory (/app/data/settings.json in Docker,
~/.thinktool/data/settings.json locally). This survives Docker restarts and rebuilds
because the data directory is a host bind-mount.

Priority (highest wins):
    1. settings.json (persistent, editable via the frontend Settings panel)
    2. Environment variables (set via .env / docker-compose)
    3. Hard-coded defaults in config.py
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Data directory — same logic as config.py so the file lands on the volume
if os.environ.get("THINKTOOL_DOCKER", "0") == "1":
    DATA_DIR = Path("/app/data")
else:
    DATA_DIR = Path(
        os.environ.get(
            "THINKTOOL_DATA_DIR",
            os.path.expanduser("~/.thinktool/data"),
        )
    )

SETTINGS_PATH = DATA_DIR / "settings.json"
_lock = Lock()


# ── low-level file I/O ──────────────────────────────────────────────────────

def _load_raw() -> Dict[str, Any]:
    """Return the parsed JSON dict, or {} if the file doesn't exist."""
    try:
        if SETTINGS_PATH.exists():
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to load settings.json – returning empty dict")
    return {}


def _save_raw(data: Dict[str, Any]) -> None:
    """Atomically write the dict to settings.json."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(SETTINGS_PATH)
    except Exception:
        logger.exception("Failed to save settings.json")
        raise RuntimeError("Failed to persist settings — check disk space and permissions.") from None


# ── public API ──────────────────────────────────────────────────────────────

def get_all_settings() -> Dict[str, Any]:
    """Return every stored key with API keys replaced by booleans.

    The frontend only needs to know *whether* a key is configured, not the
    actual secret.  Real keys never leave the server.
    """
    with _lock:
        raw = _load_raw()
    data = _deep_copy(raw)
    # Replace api_key strings with booleans: True = configured, absent = not
    for k in list(data.get("api_keys", {})):
        data["api_keys"][k] = bool(data["api_keys"][k])
    for cp in data.get("custom_providers", []):
        if isinstance(cp, dict):
            cp["api_key"] = bool(cp.get("api_key"))
    return data


def get_setting(key: str, default: Any = None) -> Any:
    """Read a single top-level key from settings.json."""
    with _lock:
        raw = _load_raw()
    return raw.get(key, default)


def update_settings(partial: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge *partial* into the existing settings and persist.

    Boolean ``True`` values in ``api_keys`` or ``custom_providers[*].api_key``
    are treated as placeholders ("keep the existing key") and are replaced
    with the currently-stored value before saving.

    Returns the full merged dict (with API keys masked).
    """
    with _lock:
        current = _load_raw()

        # Remember existing keys so booleans can be resolved
        saved_api_keys = dict(current.get("api_keys", {}))
        saved_custom = list(current.get("custom_providers", []))
        saved_custom_keys = {
            cp.get("id"): cp.get("api_key", "")
            for cp in saved_custom if isinstance(cp, dict)
        }

        _deep_merge(current, partial)

        # Resolve boolean placeholders in api_keys
        for k, v in list(current.get("api_keys", {}).items()):
            if v is True:
                if k in saved_api_keys and saved_api_keys[k]:
                    current["api_keys"][k] = saved_api_keys[k]
                else:
                    del current["api_keys"][k]  # placeholder but no stored key

        # Resolve boolean placeholders in custom_providers
        for cp in current.get("custom_providers", []):
            if isinstance(cp, dict) and cp.get("api_key") is True:
                real = saved_custom_keys.get(cp.get("id"), "")
                if real:
                    cp["api_key"] = real
                else:
                    cp["api_key"] = ""


        # Prune empty values so the file stays clean
        _prune_empty(current)
        _save_raw(current)
        return _masked_copy(current)


def get_api_key(provider_id: str) -> str:
    """
    Return the API key for *provider_id*.

    Checks (in order):
        1. settings.json → api_keys → <provider_id>  (Settings UI wins)
        2. Environment variable (uppercase, e.g. DEEPSEEK_API_KEY)
        3. Empty string
    """
    # Settings UI takes priority over environment variables
    with _lock:
        raw = _load_raw()
    api_keys = raw.get("api_keys", {})
    settings_val = api_keys.get(provider_id, "")
    if settings_val:
        return settings_val

    # Fall back to environment variable
    env_var = f"{provider_id.upper()}_API_KEY"
    env_val = os.environ.get(env_var, "")
    if env_val:
        return env_val

    return ""


def get_custom_providers() -> List[Dict[str, Any]]:
    """Return the list of user-defined custom providers."""
    with _lock:
        raw = _load_raw()
    return list(raw.get("custom_providers", []))


def get_setting_override(provider_id: str, field: str) -> Optional[str]:
    """
    Check whether the user has overridden a per-provider field
    (base_url or model) in settings.json.
    """
    with _lock:
        raw = _load_raw()
    overrides = raw.get("provider_overrides", {})
    prov = overrides.get(provider_id, {})
    return prov.get(field)


def get_other_settings() -> Dict[str, Any]:
    """Return miscellaneous top-level settings."""
    with _lock:
        raw = _load_raw()
    return raw.get("other", {})


# ── helpers ─────────────────────────────────────────────────────────────────

def _deep_merge(base: Dict, updates: Dict) -> None:
    """Recursively merge *updates* into *base* (mutates base)."""
    for key, val in updates.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val


def _prune_empty(d: Dict) -> None:
    """Remove empty strings, empty dicts, and empty lists from *d* (recursive)."""
    for key in list(d.keys()):
        v = d[key]
        if isinstance(v, dict):
            _prune_empty(v)
            if not v:
                del d[key]
        elif isinstance(v, list):
            d[key] = [item for item in v if not _is_empty(item)]
            if not d[key]:
                del d[key]
        elif _is_empty(v):
            del d[key]


def _is_empty(v: Any) -> bool:
    """Return True if *v* is an "empty" value we should prune."""
    if v is None:
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def _deep_copy(obj: Any) -> Any:
    """Cheap deep copy via json round-trip (safe for our simple types)."""
    return json.loads(json.dumps(obj, ensure_ascii=False))


def _masked_copy(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a copy where every 'api_key' value is masked.
    Never sends real keys back to the frontend.
    """
    copy = _deep_copy(data)

    def _mask(d: dict) -> None:
        for k, v in d.items():
            if k == "api_key" and isinstance(v, str) and v.strip():
                # Keep first 4 and last 4 chars
                if len(v) > 8:
                    d[k] = v[:4] + "****" + v[-4:]
                else:
                    d[k] = "****"
            elif isinstance(v, dict):
                _mask(v)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        _mask(item)

    _mask(copy)
    return copy
