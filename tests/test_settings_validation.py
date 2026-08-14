"""Regression: Save Settings must not demand a re-typed provider key.

Bug: a provider that already has a stored API key shows an empty field
(the real secret is never sent to the browser). Save used to treat that
empty field as "API key required" and refuse to save.

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
    "apiKeyRequired": "API key required",
}


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


def provider_key_is_required(provider: dict | None) -> bool:
    """Mirror of ``providerKeyIsRequired`` in settingsValidation.js."""
    return not bool((provider or {}).get("preconfigured"))


def validate_providers(providers, messages=_MESSAGES) -> dict:
    """Mirror of ``validateProviders`` in settingsValidation.js."""
    errors: dict[str, str] = {}
    for p in providers or []:
        key = p.get("errorKey")
        if not p.get("preconfigured"):
            if not str((p or {}).get("name") or "").strip():
                errors[key] = messages["nameRequired"]
            if not str((p or {}).get("base_url") or "").strip():
                errors[key] = errors.get(key) or messages["baseUrlRequired"]
        if provider_key_is_required(p) and not provider_has_usable_key(
            p.get("api_key"), p.get("keyConfigured")
        ):
            errors[key] = errors.get(key) or messages["apiKeyRequired"]
    return errors


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
    assert provider_has_usable_key("", True) is True


def test_save_rejects_new_custom_provider_with_no_key():
    """A newly added card (no stored key) still requires an API key."""
    errors = validate_providers([_custom()])
    assert errors == {"custom-0": "API key required"}


def test_typed_override_counts_as_present():
    assert validate_providers([_custom(api_key="sk-dummy")]) == {}


def test_raw_get_shape_boolean_true_counts_as_present():
    """GET /api/settings returns api_key: true before the panel blanks it."""
    assert provider_has_usable_key(True, False) is True
    assert validate_providers([_custom(api_key=True)]) == {}


def test_builtin_empty_stored_key_does_not_block_save():
    """Same usable-key rule; pre-configured cards do not require a key."""
    assert validate_providers([_builtin(keyConfigured=True)]) == {}
    assert validate_providers([_builtin()]) == {}


def test_mixed_list_uses_one_function():
    """Built-in (empty, unused) + custom (stored key) must both pass."""
    errors = validate_providers([
        _builtin(),
        _custom(errorKey="custom-0", keyConfigured=True),
    ])
    assert errors == {}


def test_mixed_list_still_rejects_incomplete_custom():
    errors = validate_providers([
        _builtin(keyConfigured=True),
        _custom(errorKey="custom-0"),
    ])
    assert errors == {"custom-0": "API key required"}


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


def test_preconfigured_skips_name_and_url_checks():
    errors = validate_providers([_builtin(name="", base_url="")])
    assert errors == {}


# --------------------------------------------------------------------------- source lock — the JS helper is what the panel actually runs


def test_js_helper_treats_stored_key_as_usable():
    src = _HELPER.read_text(encoding="utf-8")
    assert "export function providerHasUsableKey" in src
    assert "export function encodeStoredApiKey" in src
    assert "export function validateProviders" in src
    assert "export function providerKeyIsRequired" in src
    assert "apiKey === true" in src
    assert "keyConfigured" in src
    assert "providerHasUsableKey(p.api_key, p.keyConfigured)" in src


def test_settings_panel_uses_shared_validator():
    src = _PANEL.read_text(encoding="utf-8")
    assert "validateProviders" in src
    assert "encodeStoredApiKey" in src
    assert "from '../utils/settingsValidation.js'" in src
    validate_start = src.index("const validate = () =>")
    validate_end = src.index("const handleSave")
    body = src[validate_start:validate_end]
    assert "validateProviders" in body
    assert "BUILTIN_PROVIDERS.map" in body
    assert "preconfigured: true" in body
    assert "preconfigured: false" in body
    assert "api_key?.trim()" not in body
    assert "validateCustomProviders" not in src
