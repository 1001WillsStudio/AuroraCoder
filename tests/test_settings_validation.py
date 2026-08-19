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

Max Tool Concurrency (1–20), Terminal Max Output (1000–100000), and
Web Secondary Max Tokens (256–32768) are checked the same way — HTML
min/max alone used to let Save persist 50 / 1 / 10.
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
    assert "export function validateMaxToolConcurrency" in src
    assert "export function validateTerminalMaxOutput" in src
    assert "export function validateWebSecondaryMaxTokens" in src
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
    assert "validateMaxToolConcurrency" in body
    assert "validateTerminalMaxOutput" in body
    assert "validateWebSecondaryMaxTokens" in body
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


def test_handle_save_rejects_out_of_range_concurrency_and_terminal_before_put():
    """Reported case: Save must not PUT concurrency 50 or terminal output 1."""
    src = _PANEL.read_text(encoding="utf-8")
    save_start = src.index("const handleSave = async () =>")
    save_end = src.index("const builtIn = providers.filter")
    body = src[save_start:save_end]
    assert "validateMaxToolConcurrency" in body
    assert "validateTerminalMaxOutput" in body
    assert "validateWebSecondaryMaxTokens" in body
    assert "updateSettings" in body
    assert body.index("validateMaxToolConcurrency") < body.index("updateSettings")
    assert body.index("validateTerminalMaxOutput") < body.index("updateSettings")
    assert body.index("validateWebSecondaryMaxTokens") < body.index("updateSettings")
    assert "return" in body
    assert 'min="1"' in src and 'max="20"' in src
    assert 'min="1000"' in src and 'max="100000"' in src
    assert 'min="256"' in src and 'max="32768"' in src


def test_max_iterations_range_message_is_translated():
    translations = _ROOT / "frontend" / "src" / "i18n" / "translations.js"
    src = translations.read_text(encoding="utf-8")
    assert "msg.maxIterationsRange" in src
    assert "must be between 5 and 200" in src


def validate_integer_range(value, min_v, max_v, error_key):
    """Mirror of ``validateIntegerRange`` in settingsValidation.js."""
    if value in ("", None):
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return error_key
    if n != int(n) or n < min_v or n > max_v:
        return error_key
    return None


@pytest.mark.parametrize(
    "value, expected",
    [
        ("50", "msg.maxToolConcurrencyRange"),  # reported case
        (50, "msg.maxToolConcurrencyRange"),
        ("0", "msg.maxToolConcurrencyRange"),
        ("21", "msg.maxToolConcurrencyRange"),
        ("abc", "msg.maxToolConcurrencyRange"),
        (1.5, "msg.maxToolConcurrencyRange"),
        ("1", None),
        (1, None),
        ("5", None),
        (20, None),
        ("", None),
        (None, None),
        ("   ", None),
    ],
)
def test_validate_max_tool_concurrency(value, expected):
    assert validate_integer_range(value, 1, 20, "msg.maxToolConcurrencyRange") == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("1", "msg.terminalMaxOutputRange"),  # reported case
        (1, "msg.terminalMaxOutputRange"),
        ("999", "msg.terminalMaxOutputRange"),
        ("100001", "msg.terminalMaxOutputRange"),
        ("abc", "msg.terminalMaxOutputRange"),
        (1500.5, "msg.terminalMaxOutputRange"),
        ("1000", None),
        (1000, None),
        ("15000", None),
        (100000, None),
        ("", None),
        (None, None),
    ],
)
def test_validate_terminal_max_output(value, expected):
    assert validate_integer_range(value, 1000, 100000, "msg.terminalMaxOutputRange") == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("10", "msg.webSecondaryMaxTokensRange"),  # related check
        (10, "msg.webSecondaryMaxTokensRange"),
        ("255", "msg.webSecondaryMaxTokensRange"),
        ("32769", "msg.webSecondaryMaxTokensRange"),
        ("256", None),
        (4096, None),
        (32768, None),
        ("", None),
        (None, None),
    ],
)
def test_validate_web_secondary_max_tokens(value, expected):
    assert validate_integer_range(value, 256, 32768, "msg.webSecondaryMaxTokensRange") == expected


def test_numeric_range_messages_are_translated():
    translations = _ROOT / "frontend" / "src" / "i18n" / "translations.js"
    src = translations.read_text(encoding="utf-8")
    assert "msg.maxToolConcurrencyRange" in src
    assert "must be between 1 and 20" in src
    assert "msg.terminalMaxOutputRange" in src
    assert "must be between 1000 and 100000" in src
    assert "msg.webSecondaryMaxTokensRange" in src
    assert "must be between 256 and 32768" in src
