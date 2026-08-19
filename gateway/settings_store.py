"""
Persistent user settings store.

Reads/writes a JSON file in the data directory (/app/data/settings.json in Docker,
~/.auroracoder/data/settings.json locally). This survives Docker restarts and rebuilds
because the data directory is a host bind-mount.

Priority (highest wins):
    1. settings.json (persistent, editable via the frontend Settings panel)
    2. Environment variables (set via .env)
    3. Hard-coded defaults in config.py
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
from threading import Lock
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Data directory — same logic as config.py so the file lands on the volume
if os.environ.get("AURORACODER_DOCKER", "0") == "1":
    DATA_DIR = Path("/app/data")
else:
    DATA_DIR = Path(
        os.environ.get(
            "AURORACODER_DATA_DIR",
            os.path.expanduser("~/.auroracoder/data"),
        )
    )

SETTINGS_PATH = DATA_DIR / "settings.json"
_lock = Lock()

# Agent Behavior → Max Iterations Per Turn. Matches the Settings spinbutton
# (min=5, max=200). HTML constraints are not enforced by the Save button, so
# the store is the last line of defence against a persisted 0 (or 201, …).
# "unlimited" is an explicit sentinel (not 0) for long-running turns.
AGENT_MAX_ITERATIONS_MIN = 5
AGENT_MAX_ITERATIONS_MAX = 200
AGENT_MAX_ITERATIONS_UNLIMITED = "unlimited"
# Runtime stand-in so the int-only agent loop does not need a None path.
UNLIMITED_AGENT_ITERATIONS = 1_000_000
_MAX_ITERATIONS_RANGE_MSG = (
    f"max_iterations must be between {AGENT_MAX_ITERATIONS_MIN} and {AGENT_MAX_ITERATIONS_MAX}"
)


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
    # Normalize old variant keys → family keys so the frontend sees them
    _normalize_api_keys()
    # Rename legacy per-family provider keys → model-selection keys
    _normalize_model_selections()
    # Re-read after migrations so callers see the renamed keys
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

    Raises:
        ValueError: if ``other.agent.max_iterations`` is present and not an
            integer in ``[5, 200]`` or the sentinel ``"unlimited"``, or if
            a custom provider ``base_url`` is not an http(s) URL.
            The on-disk file is not written.
    """
    _validate_agent_max_iterations(partial)
    _validate_custom_provider_base_urls(partial)
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

        # Return the SAME masked format as GET /api/settings.
        # Never leak real API keys in responses.
        data = _deep_copy(current)
        for k in list(data.get("api_keys", {})):
            data["api_keys"][k] = bool(data["api_keys"][k])
        for cp in data.get("custom_providers", []):
            if isinstance(cp, dict):
                cp["api_key"] = bool(cp.get("api_key"))
        return data


# Provider-family mapping for backward compatibility.
# Old per-variant keys (deepseek-flash, opencode-ds-v4-pro, etc.)
# get merged into the three families.
_PROVIDER_FAMILIES = {
    "deepseek": ["deepseek", "deepseek-flash"],
    "opencode": ["opencode", "opencode-ds-v4-pro", "opencode-ds-v4-flash"],
    "nvidia":   ["nvidia", "nvidia-fast", "nvidia-glm5", "nvidia-glm5-fast"],
}
# Reverse: variant → family
_VARIANT_TO_FAMILY = {
    v: f for f, variants in _PROVIDER_FAMILIES.items() for v in variants
}


def _normalize_api_keys():
    """Migrate old per-variant api_keys → new per-family format.

    Called on every read so old settings are transparently upgraded.
    The on-disk file is only mutated when a new key is persisted.
    """
    raw = _load_raw()
    api_keys = raw.get("api_keys", {})
    changed = False
    for family, variants in _PROVIDER_FAMILIES.items():
        # Promote the first non-empty legacy variant key to the family key
        # (only if the family key is not already set — the family key wins).
        if not (family in api_keys and api_keys[family]):
            for v in variants:
                if v != family and v in api_keys and api_keys[v]:
                    api_keys[family] = api_keys[v]
                    changed = True
                    break
        # The family key is now canonical — delete the legacy variant keys so
        # the on-disk file is cleanly migrated and no runtime fallback is
        # needed to read them later.
        for v in variants:
            if v != family and v in api_keys:
                del api_keys[v]
                changed = True
    if changed:
        _save_raw(raw)


def _normalize_model_selections():
    """Migrate model-selection settings to the structured form.

    Canonical on-disk form (no composite-string parsing needed by readers):

        other.agent.default_model   = {"provider": "<family>", "model": "<id>"}
        other.web_secondary.model  = {"provider": "<family>", "model": "<id>"}

    ``model`` ("" or the key absent) ⇒ "auto / first enabled model" for the
    provider.  An absent or empty-structured value ⇒ system default (agent)
    or "same as agent" (web secondary).

    Absorbs every prior representation so readers only ever see the structured
    dict:
        • already-structured dict   → keys normalized ("provider", "model")
        • composite "a::b" string  → {"provider":"a","model":"b"}
        • bare family/variant str  → {"provider": <family>, "model": ""}
        • legacy default_provider / web_secondary.provider → structured

    Idempotent; the on-disk file is only rewritten when a change occurs.
    """
    def _to_structured(value):
        if isinstance(value, dict):
            prov = (value.get("provider") or "").strip()
            if not prov:
                return {}
            return {"provider": prov, "model": (value.get("model") or "").strip()}
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return {}
            if "::" in s:
                prov, mid = s.split("::", 1)
                prov = prov.strip(); mid = mid.strip()
                if not prov:
                    return {}
                return {"provider": _VARIANT_TO_FAMILY.get(prov, prov), "model": mid}
            return {"provider": _VARIANT_TO_FAMILY.get(s, s), "model": ""}
        return {}

    raw = _load_raw()
    other = raw.get("other")
    if not isinstance(other, dict):
        return
    changed = False

    def _migrate(block, key, legacy_key):
        nonlocal changed
        if not isinstance(block, dict):
            return
        value = block.get(key) if key in block else None
        if legacy_key and legacy_key in block:
            legacy = block.pop(legacy_key)
            changed = True
            if value in (None, "", {}):
                value = legacy
        structured = _to_structured(value)
        if structured:
            if block.get(key) != structured:
                block[key] = structured
                changed = True
        else:
            if key in block:
                del block[key]
                changed = True

    _migrate(other.get("agent"), "default_model", "default_provider")
    _migrate(other.get("web_secondary"), "model", "provider")
    if changed:
        _save_raw(raw)


def get_api_key(provider_id: str) -> str:
    """
    Return the API key for *provider_id*.

    Checks (in order):
        1. settings.json → custom_providers → <provider_id>
        2. settings.json → api_keys → <provider_id>  (Settings UI wins)
        3. Environment variable (uppercase, e.g. DEEPSEEK_API_KEY)
        4. Empty string
    """
    _normalize_api_keys()

    with _lock:
        raw = _load_raw()

    # 1) Check custom_providers first
    for cp in raw.get("custom_providers", []):
        if isinstance(cp, dict) and cp.get("id") == provider_id:
            key = cp.get("api_key", "")
            if key and key is not True:
                return key

    # 2) Settings UI api_keys takes priority over environment variables
    api_keys = raw.get("api_keys", {})
    settings_val = api_keys.get(provider_id, "")
    if settings_val and settings_val is not True:
        return settings_val


    # 3) Fall back to environment variable (supports _API_KEY and GitHub's _TOKEN convention)
    env_var = f"{provider_id.upper()}_API_KEY"
    env_val = os.environ.get(env_var, "")
    if env_val:
        return env_val

    # Special case: GITHUB_TOKEN
    if provider_id.lower() == "github":
        github_token = os.environ.get("GITHUB_TOKEN", "")
        if github_token:
            return github_token

    return ""


# ── GitHub auth ─────────────────────────────────────────────────────────────

def configure_github_auth():
    """If a GitHub PAT is saved in settings, configure git so that push/clone
    to github.com works without prompting for credentials.

    Uses the same url.insteadOf trick as docker/entrypoint.sh.
    Safe to call repeatedly — git config is idempotent."""
    token = get_api_key("github")
    if not token:
        return
    try:
        subprocess.run(
            ["git", "config", "--global",
             f"url.https://oauth2:{token}@github.com/.insteadOf",
             "https://github.com/"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        logger.info("GitHub auth: configured for all github.com repos (via settings).")
    except Exception as exc:
        logger.warning(f"GitHub auth: git config failed — {exc}")


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

def _is_unlimited_max_iterations(raw: Any) -> bool:
    return isinstance(raw, str) and raw.strip().lower() == AGENT_MAX_ITERATIONS_UNLIMITED


def _parse_agent_max_iterations(raw: Any) -> Optional[int]:
    """Return an int if *raw* is a whole number; None if empty; else raise.

    Accepts int, integer-valued float (``5.0``), and digit strings
    (``"30"``, ``"5.0"``). Rejects bools, non-numeric strings, and
    non-integer floats. Range is checked by the caller.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError(_MAX_ITERATIONS_RANGE_MSG)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if raw.is_integer():
            return int(raw)
        raise ValueError(_MAX_ITERATIONS_RANGE_MSG)
    if isinstance(raw, str):
        s = raw.strip()
        if s == "":
            return None
        try:
            n = float(s)
        except ValueError:
            raise ValueError(_MAX_ITERATIONS_RANGE_MSG) from None
        if not n.is_integer():
            raise ValueError(_MAX_ITERATIONS_RANGE_MSG)
        return int(n)
    raise ValueError(_MAX_ITERATIONS_RANGE_MSG)


def _validate_custom_provider_base_urls(partial: Dict[str, Any]) -> None:
    """Raise if a typed custom-provider Base URL is not http(s) with a host."""
    providers = partial.get("custom_providers")
    if not isinstance(providers, list):
        return
    for cp in providers:
        raw = cp.get("base_url") if isinstance(cp, dict) else None
        if not isinstance(raw, str) or not raw.strip():
            continue
        parsed = urlparse(raw.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("base_url must be an http:// or https:// URL")


def _validate_agent_max_iterations(partial: Dict[str, Any]) -> None:
    """Raise ValueError if the update sets max_iterations outside [5, 200]."""
    other = partial.get("other")
    if not isinstance(other, dict):
        return
    agent = other.get("agent")
    if not isinstance(agent, dict) or "max_iterations" not in agent:
        return
    raw = agent["max_iterations"]
    if _is_unlimited_max_iterations(raw):
        return
    n = _parse_agent_max_iterations(raw)
    if n is None:
        return
    if n < AGENT_MAX_ITERATIONS_MIN or n > AGENT_MAX_ITERATIONS_MAX:
        raise ValueError(_MAX_ITERATIONS_RANGE_MSG)


def clamp_agent_max_iterations(raw: Any, default: int = 30) -> int:
    """Coerce a stored/env value to an int in ``[5, 200]``.

    ``"unlimited"`` becomes ``UNLIMITED_AGENT_ITERATIONS`` so the agent
    loop (which takes an int) can run a long-lived turn. A leftover ``0``
    is lifted to 5 so it cannot stop the loop on the first turn.
    """
    if _is_unlimited_max_iterations(raw):
        return UNLIMITED_AGENT_ITERATIONS
    try:
        n = _parse_agent_max_iterations(raw)
    except ValueError:
        n = None
    if n is None:
        if _is_unlimited_max_iterations(default):
            return UNLIMITED_AGENT_ITERATIONS
        try:
            n = int(default)
        except (TypeError, ValueError):
            n = 30
    return max(AGENT_MAX_ITERATIONS_MIN, min(AGENT_MAX_ITERATIONS_MAX, n))


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
