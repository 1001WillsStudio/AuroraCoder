"""Regression tests for Settings save validation.

API keys: a provider that already has a stored key shows an empty field
(the real secret is never sent to the browser). ``encodeStoredApiKey``
turns empty + already stored into ``true`` (keep the secret). Completeness
is not checked — a provider without a key cannot discover models and
stays unused.

Max Iterations Per Turn: the spinbutton has min=5 max=200, but HTML
constraints are not enforced by the Save button. ``validateMaxIterations``
rejects 0 (and any other out-of-range value) before PUT. The sentinel
``unlimited`` is allowed (Unlimited checkbox).

``validateSettingsIntRanges`` applies the same Save-button check to
concurrency (1–20), terminal output (1000–100000), and web-secondary
tokens (256–32768).
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


def test_js_helper_exports_encode_and_max_iterations():
    src = _HELPER.read_text(encoding="utf-8")
    assert "export function encodeStoredApiKey" in src
    assert "export function validateMaxIterations" in src
    assert "export function isUnlimitedMaxIterations" in src
    assert "export function validateSettingsIntRanges" in src
    assert "max_tool_concurrency" in src
    assert "terminal_max_output" in src
    # Old "API key required" helpers must stay gone — a blank stored key
    # is not a reason to block Save.
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
    assert "validateMaxIterations" in body
    assert "validateSettingsIntRanges" in body
    assert "if (!validate())" not in body
    assert "runChecks" not in src
    assert "validateProviders" not in src
    assert "api_key?.trim()" not in body
    assert "msg.validationError" not in body


def validate_max_iterations(value):
    """Mirror of ``validateMaxIterations`` in settingsValidation.js."""
    if value in ("", None):
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    if isinstance(value, str) and value.strip().lower() == "unlimited":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "msg.maxIterationsRange"
    if n != int(n) or n < 5 or n > 200:
        return "msg.maxIterationsRange"
    return None


@pytest.mark.parametrize(
    "value, expected",
    [
        ("0", "msg.maxIterationsRange"),  # reported case
        (0, "msg.maxIterationsRange"),
        ("4", "msg.maxIterationsRange"),
        ("201", "msg.maxIterationsRange"),
        ("abc", "msg.maxIterationsRange"),
        (5.5, "msg.maxIterationsRange"),
        ("5", None),
        (5, None),
        ("30", None),
        (200, None),
        ("", None),
        (None, None),
        ("   ", None),
        ("unlimited", None),
        ("Unlimited", None),
    ],
)
def test_validate_max_iterations(value, expected):
    assert validate_max_iterations(value) == expected


def test_handle_save_rejects_out_of_range_iterations_before_put():
    """Save must not PUT when Max Iterations Per Turn is 0 (or otherwise out of range)."""
    src = _PANEL.read_text(encoding="utf-8")
    save_start = src.index("const handleSave = async () =>")
    save_end = src.index("const builtIn = providers.filter")
    body = src[save_start:save_end]
    assert "validateMaxIterations" in body
    assert "updateSettings" in body
    # Range check runs first; a failure returns without saving.
    assert body.index("validateMaxIterations") < body.index("updateSettings")
    assert "return" in body
    assert "min=\"5\"" in src
    assert "max=\"200\"" in src
    assert "MAX_ITERATIONS_UNLIMITED" in src
    assert "isUnlimitedMaxIterations" in src
    assert "agent.maxIterationsUnlimited" in src


def test_max_iterations_range_message_is_translated():
    translations = _ROOT / "frontend" / "src" / "i18n" / "translations.js"
    src = translations.read_text(encoding="utf-8")
    assert "msg.maxIterationsRange" in src
    assert "must be between 5 and 200" in src


def validate_settings_int_ranges(settings):
    """Mirror of ``validateSettingsIntRanges`` in settingsValidation.js."""
    fields = (
        (("other", "agent", "max_tool_concurrency"), 1, 20, "msg.maxToolConcurrencyRange"),
        (("other", "agent", "terminal_max_output"), 1000, 100000, "msg.terminalMaxOutputRange"),
        (("other", "web_secondary", "max_tokens"), 256, 32768, "msg.webSecondaryMaxTokensRange"),
    )
    for path, min_v, max_v, error_key in fields:
        value = settings
        for key in path:
            value = (value or {}).get(key) if isinstance(value, dict) else None
        if value in ("", None) or (isinstance(value, str) and value.strip() == ""):
            continue
        try:
            n = float(value)
        except (TypeError, ValueError):
            return error_key
        if n != int(n) or n < min_v or n > max_v:
            return error_key
    return None


def test_validate_settings_int_ranges_reported_cases():
    assert validate_settings_int_ranges(
        {"other": {"agent": {"max_tool_concurrency": "50"}}}
    ) == "msg.maxToolConcurrencyRange"
    assert validate_settings_int_ranges(
        {"other": {"agent": {"terminal_max_output": "1"}}}
    ) == "msg.terminalMaxOutputRange"
    assert validate_settings_int_ranges(
        {"other": {"web_secondary": {"max_tokens": "10"}}}
    ) == "msg.webSecondaryMaxTokensRange"
    assert validate_settings_int_ranges({
        "other": {"agent": {"max_tool_concurrency": "5", "terminal_max_output": "15000"}},
    }) is None


def test_handle_save_rejects_out_of_range_concurrency_and_terminal_before_put():
    """Reported case: Save must not PUT concurrency 50 or terminal output 1."""
    src = _PANEL.read_text(encoding="utf-8")
    save_start = src.index("const handleSave = async () =>")
    save_end = src.index("const builtIn = providers.filter")
    body = src[save_start:save_end]
    assert "validateSettingsIntRanges" in body
    assert "updateSettings" in body
    assert body.index("validateSettingsIntRanges") < body.index("updateSettings")
    assert "return" in body
    assert 'min="1"' in src and 'max="20"' in src
    assert 'min="1000"' in src and 'max="100000"' in src
    assert 'min="256"' in src and 'max="32768"' in src


def test_numeric_range_messages_are_translated():
    translations = _ROOT / "frontend" / "src" / "i18n" / "translations.js"
    src = translations.read_text(encoding="utf-8")
    assert "msg.maxToolConcurrencyRange" in src
    assert "must be between 1 and 20" in src
    assert "msg.terminalMaxOutputRange" in src
    assert "must be between 1000 and 100000" in src
    assert "msg.webSecondaryMaxTokensRange" in src
    assert "must be between 256 and 32768" in src
