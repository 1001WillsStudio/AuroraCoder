"""Regression: Save Settings must not demand a re-typed custom-provider key.

Bug: a provider that already has a stored API key shows an empty field
(the real secret is never sent to the browser). Save used to treat that
empty field as "API key required" and refuse to save.

``frontend/src/utils/settingsValidation.js`` is the check the panel runs.
This file mirrors that check and also asserts the panel still calls it.
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


def custom_provider_has_usable_key(cp: dict | None) -> bool:
    """Mirror of ``customProviderHasUsableKey`` in settingsValidation.js."""
    if not cp:
        return False
    if cp.get("api_key") is True:
        return True
    typed = cp.get("api_key")
    if isinstance(typed, str) and typed.strip():
        return True
    return bool(cp.get("_key_configured"))


def validate_custom_providers(custom_providers, messages=_MESSAGES) -> dict:
    """Mirror of ``validateCustomProviders`` in settingsValidation.js."""
    errors: dict[str, str] = {}
    for i, cp in enumerate(custom_providers or []):
        key = f"custom-{i}"
        if not str((cp or {}).get("name") or "").strip():
            errors[key] = messages["nameRequired"]
        if not str((cp or {}).get("base_url") or "").strip():
            errors[key] = errors.get(key) or messages["baseUrlRequired"]
        if not custom_provider_has_usable_key(cp):
            errors[key] = errors.get(key) or messages["apiKeyRequired"]
    return errors


# --------------------------------------------------------------------------- reported bug


def test_save_allows_empty_override_when_custom_key_already_stored():
    """Explorer repro: Mock LLM (E2E) field empty, placeholder says key is set.

    Fresh load → custom_providers[mockllm] has api_key blanked and
    ``_key_configured=true``. Save with no edits must not be rejected.
    """
    providers = [
        {
            "id": "mockllm",
            "name": "Mock LLM (E2E)",
            "base_url": "http://mock-llm:8080/v1",
            "api_key": "",
            "_key_configured": True,
        }
    ]
    errors = validate_custom_providers(providers)
    assert errors == {}
    assert custom_provider_has_usable_key(providers[0]) is True


def test_save_rejects_new_custom_provider_with_no_key():
    """A newly added card (no stored key) still requires an API key."""
    providers = [
        {
            "id": "custom-1",
            "name": "My Provider",
            "base_url": "https://api.example.com/v1",
            "api_key": "",
        }
    ]
    errors = validate_custom_providers(providers)
    assert errors == {"custom-0": "API key required"}


def test_typed_override_counts_as_present():
    providers = [
        {
            "id": "mockllm",
            "name": "Mock LLM (E2E)",
            "base_url": "http://mock-llm:8080/v1",
            "api_key": "sk-dummy",
        }
    ]
    assert validate_custom_providers(providers) == {}


def test_raw_get_shape_boolean_true_counts_as_present():
    """GET /api/settings returns api_key: true before the panel blanks it."""
    cp = {
        "id": "mockllm",
        "name": "Mock LLM (E2E)",
        "base_url": "http://mock-llm:8080/v1",
        "api_key": True,
    }
    assert custom_provider_has_usable_key(cp) is True
    assert validate_custom_providers([cp]) == {}


@pytest.mark.parametrize(
    "cp, expected",
    [
        (None, False),
        ({}, False),
        ({"api_key": "   "}, False),
        ({"api_key": False}, False),
        ({"_key_configured": False, "api_key": ""}, False),
        ({"_key_configured": True, "api_key": ""}, True),
        ({"api_key": True}, True),
        ({"api_key": "sk-x"}, True),
    ],
)
def test_usable_key_predicate(cp, expected):
    assert custom_provider_has_usable_key(cp) is expected


def test_name_and_base_url_still_required_even_with_stored_key():
    errors = validate_custom_providers(
        [{"id": "x", "name": "", "base_url": "", "api_key": "", "_key_configured": True}]
    )
    assert errors["custom-0"] == "Name required"


# --------------------------------------------------------------------------- source lock — the JS helper is what the panel actually runs


def test_js_helper_treats_stored_key_as_usable():
    src = _HELPER.read_text(encoding="utf-8")
    assert "export function customProviderHasUsableKey" in src
    assert "export function validateCustomProviders" in src
    assert "cp.api_key === true" in src
    assert "cp._key_configured" in src
    # The old panel check (`!cp.api_key?.trim()`) must not be the only gate.
    assert "customProviderHasUsableKey(cp)" in src


def test_settings_panel_uses_extracted_validator():
    src = _PANEL.read_text(encoding="utf-8")
    assert "validateCustomProviders" in src
    assert "from '../utils/settingsValidation.js'" in src
    # Do not regress to the empty-override-is-missing check.
    validate_start = src.index("const validate = () =>")
    validate_end = src.index("const handleSave")
    body = src[validate_start:validate_end]
    assert "validateCustomProviders" in body
    assert "api_key?.trim()" not in body
