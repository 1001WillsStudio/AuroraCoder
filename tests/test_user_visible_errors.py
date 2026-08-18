"""Provider failures must show a short sentence, not an SDK/JSON dump.

The explorer case: sending ``e2e:error`` makes the mock provider return HTTP
500. The OpenAI client stringifies that as
``Error code: 500 - {'error': {'message': '...', 'type': '...'}}``, and that
blob was rendered as the assistant chat message.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from src.user_visible_errors import (
    PROVIDER_FAILURE_MESSAGE,
    looks_like_raw_provider_error,
    user_visible_error_message,
)

ROOT = Path(__file__).resolve().parent.parent
JS_HELPER = ROOT / "frontend" / "src" / "utils" / "providerError.js"
STREAM_HOOK = ROOT / "frontend" / "src" / "hooks" / "createStreamCallbacks.js"
CHAT_MESSAGE = ROOT / "frontend" / "src" / "components" / "ChatMessage.jsx"

# Exact text the explorer saw in the assistant bubble (minus the UI "Error: " prefix).
EXPLORER_BLOB = (
    "Error code: 500 - {'error': {'message': 'mock provider: simulated "
    "upstream failure', 'type': 'mock_error'}}"
)


def _eval_js(expr: str):
    script = (
        "import { userVisibleErrorMessage, looksLikeRawProviderError, "
        "PROVIDER_FAILURE_MESSAGE } from "
        f"{json.dumps(JS_HELPER.resolve().as_uri())}\n"
        f"const out = {expr}\n"
        "console.log(JSON.stringify(out))\n"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout or "node helper failed")
    return json.loads(proc.stdout)


def test_explorer_openai_blob_is_replaced():
    assert looks_like_raw_provider_error(EXPLORER_BLOB) is True
    assert user_visible_error_message(EXPLORER_BLOB) == PROVIDER_FAILURE_MESSAGE
    assert "mock provider" not in user_visible_error_message(EXPLORER_BLOB)
    assert "Error code:" not in user_visible_error_message(EXPLORER_BLOB)


def test_prefixed_blob_and_json_object_are_replaced():
    assert user_visible_error_message(f"Error: {EXPLORER_BLOB}") == PROVIDER_FAILURE_MESSAGE
    assert user_visible_error_message(
        '{"error": {"message": "internal", "type": "api_error"}}'
    ) == PROVIDER_FAILURE_MESSAGE


def test_human_sentences_pass_through():
    assert user_visible_error_message("Cannot connect to backend") == (
        "Cannot connect to backend"
    )
    assert user_visible_error_message("provider exploded") == "provider exploded"
    assert user_visible_error_message("") == PROVIDER_FAILURE_MESSAGE
    assert user_visible_error_message(None) == PROVIDER_FAILURE_MESSAGE


def test_exception_objects_use_str_then_sanitize():
    class FakeAPIError(Exception):
        pass

    assert user_visible_error_message(FakeAPIError(EXPLORER_BLOB)) == PROVIDER_FAILURE_MESSAGE
    assert user_visible_error_message(FakeAPIError("timed out")) == "timed out"


def test_frontend_helper_matches_python_for_explorer_blob():
    assert _eval_js(f"userVisibleErrorMessage({json.dumps(EXPLORER_BLOB)})") == (
        PROVIDER_FAILURE_MESSAGE
    )
    assert _eval_js(f"looksLikeRawProviderError({json.dumps(EXPLORER_BLOB)})") is True
    assert _eval_js("userVisibleErrorMessage('Cannot connect to backend')") == (
        "Cannot connect to backend"
    )


def test_live_ui_sanitizes_error_bubble_text():
    """The on-screen assistant bubble is built here; it must not interpolate raw."""
    hook = STREAM_HOOK.read_text(encoding="utf-8")
    chat = CHAT_MESSAGE.read_text(encoding="utf-8")
    assert "userVisibleErrorMessage" in hook
    assert "Error: ${error.message}" not in hook
    assert "userVisibleErrorMessage" in chat
