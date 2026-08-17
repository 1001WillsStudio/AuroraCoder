"""Regression: Save Settings must not demand a re-typed provider key.

Bug: a provider that already has a stored API key shows an empty field
(the real secret is never sent to the browser). Save used to treat that
empty field as "API key required" and refuse to send PUT /api/settings.

The only helper is ``encodeStoredApiKey``: empty + already stored →
``true`` (keep the secret). Completeness is not checked — a provider
without a key cannot discover models and stays unused.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HELPER = _ROOT / "frontend" / "src" / "utils" / "settingsValidation.js"
_PANEL = _ROOT / "frontend" / "src" / "components" / "SettingsPanel.jsx"


def encode_stored_api_key(api_key, key_configured=False):
    """Mirror of ``encodeStoredApiKey`` in settingsValidation.js."""
    if isinstance(api_key, str) and api_key.strip():
        return api_key.strip()
    if api_key is True or bool(key_configured):
        return True
    return None


def test_empty_field_with_stored_key_encodes_as_keep():
    """Reported case: blank box, key already stored → keep, do not reject."""
    assert encode_stored_api_key("", True) is True
    assert encode_stored_api_key(None, True) is True
    assert encode_stored_api_key(True, False) is True


def test_typed_override_is_sent():
    assert encode_stored_api_key("sk-dummy", False) == "sk-dummy"
    assert encode_stored_api_key("  sk-dummy  ", True) == "sk-dummy"


def test_empty_field_without_stored_key_is_omitted():
    """No key yet: omit it. Save is not blocked; the provider stays unused."""
    assert encode_stored_api_key("", False) is None
    assert encode_stored_api_key(None, False) is None
    assert encode_stored_api_key("   ", False) is None


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


def test_js_helper_is_only_the_encode():
    src = _HELPER.read_text(encoding="utf-8")
    assert "export function encodeStoredApiKey" in src
    assert "export function validateProviders" not in src
    assert "export function collectProviderKeyWarnings" not in src
    assert "export function providerHasUsableKey" not in src
    assert "apiKeyRequired" not in src


def test_settings_panel_encodes_keys_and_does_not_block_save():
    src = _PANEL.read_text(encoding="utf-8")
    assert "encodeStoredApiKey" in src
    assert "from '../utils/settingsValidation.js'" in src
    save_start = src.index("const handleSave = async () =>")
    save_end = src.index("const builtIn = providers.filter")
    body = src[save_start:save_end]
    assert "encodeStoredApiKey" in body
    assert "if (!validate())" not in body
    assert "runChecks" not in src
    assert "validateProviders" not in src
    assert "api_key?.trim()" not in body
    assert "msg.validationError" not in body
