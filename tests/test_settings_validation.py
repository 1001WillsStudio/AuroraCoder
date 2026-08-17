"""Regression: Save Settings must not demand a re-typed provider key.

Bug: a provider that already has a stored API key shows an empty field
(the real secret is never sent to the browser). Save used to treat that
empty field as "API key required" and refuse to save.

A custom card with no key at all is a warning, not a blocker — the
provider stays unused until the user adds a key.

Built-in and custom cards share ``frontend/src/utils/settingsValidation.js``.
This file mirrors that helper and asserts the panel still calls it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HELPER = _ROOT / "frontend" / "src" / "utils" / "settingsValidation.js"
_PANEL = _ROOT / "frontend" / "src" / "components" / "SettingsPanel.jsx"

_MESSAGES = {
    "nameRequired": "Name required",
    "baseUrlRequired": "Base URL required",
}

_WARN = "No API key — this provider stays unused until you add one"


def provider_has_usable_key(api_key, key_configured=False) -> bool:
    """Mirror of ``providerHasUsableKey`` in settingsValidation.js."""
    if api_key is True:
        return True
    if isinstance(api_key, str) and api_key.strip():
        return True
    return bool(key_configured)


def encode_stored_api_key(api_key, key_configured=False):
    """Mirror of ``encodeStoredApiKey`` in settingsValidation.js."""
    if isinstance(api_key, str) and api_key.strip():
        return api_key.strip()
    if api_key is True or bool(key_configured):
        return True
    return None


def validate_providers(providers, messages=_MESSAGES) -> dict:
    """Mirror of ``validateProviders`` in settingsValidation.js."""
    errors: dict[str, str] = {}
    for p in providers or []:
        if p.get("preconfigured"):
            continue
        key = p.get("errorKey")
        if not str((p or {}).get("name") or "").strip():
            errors[key] = messages["nameRequired"]
        if not str((p or {}).get("base_url") or "").strip():
            errors[key] = errors.get(key) or messages["baseUrlRequired"]
    return errors


def collect_provider_key_warnings(providers, message=_WARN) -> dict:
    """Mirror of ``collectProviderKeyWarnings`` in settingsValidation.js."""
    warnings: dict[str, str] = {}
    for p in providers or []:
        if p.get("preconfigured"):
            continue
        started = str((p or {}).get("name") or "").strip() or str(
            (p or {}).get("base_url") or ""
        ).strip()
        if started and not provider_has_usable_key(p.get("api_key"), p.get("keyConfigured")):
            warnings[p.get("errorKey")] = message
    return warnings


def _custom(**overrides):
    row = {
        "errorKey": "custom-0",
        "name": "Mock LLM",
        "base_url": "http://mock-llm:8080/v1",
        "api_key": "",
        "keyConfigured": False,
        "preconfigured": False,
    }
    row.update(overrides)
    return row


def _builtin(**overrides):
    row = {
        "errorKey": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "keyConfigured": False,
        "preconfigured": True,
    }
    row.update(overrides)
    return row


# --------------------------------------------------------------------------- reported bug


def test_save_allows_empty_override_when_custom_key_already_stored():
    """Save with no edits must succeed when a custom provider already has a key."""
    providers = [_custom(keyConfigured=True)]
    assert validate_providers(providers) == {}
    assert collect_provider_key_warnings(providers) == {}
    assert provider_has_usable_key("", True) is True


def test_save_warns_but_allows_new_custom_provider_with_no_key():
    """A new card with no key must not block Save; it is a warning only."""
    providers = [_custom()]
    assert validate_providers(providers) == {}
    assert collect_provider_key_warnings(providers) == {"custom-0": _WARN}


def test_typed_override_counts_as_present():
    providers = [_custom(api_key="sk-dummy")]
    assert validate_providers(providers) == {}
    assert collect_provider_key_warnings(providers) == {}


def test_raw_get_shape_boolean_true_counts_as_present():
    """GET /api/settings returns api_key: true before the panel blanks it."""
    assert provider_has_usable_key(True, False) is True
    assert validate_providers([_custom(api_key=True)]) == {}
    assert collect_provider_key_warnings([_custom(api_key=True)]) == {}


def test_builtin_empty_key_does_not_block_or_warn():
    """Unused built-in cards stay silent — they are pre-configured empties."""
    assert validate_providers([_builtin(keyConfigured=True)]) == {}
    assert validate_providers([_builtin()]) == {}
    assert collect_provider_key_warnings([_builtin()]) == {}


def test_mixed_list_uses_one_function():
    """Built-in (empty) + custom (stored key) must both pass with no warning."""
    providers = [
        _builtin(),
        _custom(errorKey="custom-0", keyConfigured=True),
    ]
    assert validate_providers(providers) == {}
    assert collect_provider_key_warnings(providers) == {}


def test_mixed_list_warns_custom_without_key_but_does_not_block():
    providers = [
        _builtin(keyConfigured=True),
        _custom(errorKey="custom-0"),
    ]
    assert validate_providers(providers) == {}
    assert collect_provider_key_warnings(providers) == {"custom-0": _WARN}


@pytest.mark.parametrize(
    "api_key, key_configured, expected",
    [
        (None, False, False),
        ("", False, False),
        ("   ", False, False),
        (False, False, False),
        ("", True, True),
        (True, False, True),
        ("sk-x", False, True),
    ],
)
def test_usable_key_predicate(api_key, key_configured, expected):
    assert provider_has_usable_key(api_key, key_configured) is expected


@pytest.mark.parametrize(
    "api_key, key_configured, expected",
    [
        ("sk-new", False, "sk-new"),
        ("  sk-new  ", False, "sk-new"),
        ("", True, True),
        (True, False, True),
        ("", False, None),
        (None, False, None),
    ],
)
def test_encode_stored_api_key(api_key, key_configured, expected):
    assert encode_stored_api_key(api_key, key_configured) == expected


def test_name_and_base_url_still_required_even_with_stored_key():
    errors = validate_providers([_custom(name="", base_url="", keyConfigured=True)])
    assert errors["custom-0"] == "Name required"


def test_blank_draft_custom_card_does_not_warn():
    """An empty Add-provider draft is pruned on save; no warning yet."""
    assert collect_provider_key_warnings([_custom(name="", base_url="")]) == {}


def test_preconfigured_skips_name_and_url_checks():
    errors = validate_providers([_builtin(name="", base_url="")])
    assert errors == {}


# --------------------------------------------------------------------------- source lock — the JS helper is what the panel actually runs


def test_js_helper_treats_stored_key_as_usable_and_missing_key_as_warning():
    src = _HELPER.read_text(encoding="utf-8")
    assert "export function providerHasUsableKey" in src
    assert "export function encodeStoredApiKey" in src
    assert "export function validateProviders" in src
    assert "export function collectProviderKeyWarnings" in src
    assert "apiKey === true" in src
    assert "keyConfigured" in src
    assert "providerKeyIsRequired" not in src
    assert "messages.apiKeyRequired" not in src


def test_settings_panel_uses_shared_validator():
    src = _PANEL.read_text(encoding="utf-8")
    assert "validateProviders" in src
    assert "collectProviderKeyWarnings" in src
    assert "encodeStoredApiKey" in src
    assert "from '../utils/settingsValidation.js'" in src
    checks_start = src.index("const runChecks = () =>")
    checks_end = src.index("const handleSave")
    body = src[checks_start:checks_end]
    assert "validateProviders" in body
    assert "collectProviderKeyWarnings" in body
    assert "BUILTIN_PROVIDERS.map" in src[src.index("const providerRecords"):checks_end]
    assert "preconfigured: true" in src
    assert "preconfigured: false" in src
    assert "api_key?.trim()" not in body
    assert "validateCustomProviders" not in src
    assert "msg.savedWithKeyWarning" in src
    assert "settings-field-warning" in src
