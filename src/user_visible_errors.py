"""Turn provider/SDK exception text into a short sentence for the chat UI.

OpenAI-compatible clients stringify HTTP failures as
``Error code: 500 - {'error': {...}}``. That dump is useful in diagnostics
logs but must not appear in the assistant bubble.
"""
from __future__ import annotations

import re
from typing import Any, Optional

PROVIDER_FAILURE_MESSAGE = "The model provider failed."

_ERROR_CODE_DUMP = re.compile(r"Error code:\s*\d+\s*-\s*[{\[]", re.IGNORECASE)
_DICT_ERROR_KEY = re.compile(r"""['"]error['"]\s*:""")


def looks_like_raw_provider_error(message: Any) -> bool:
    """True when *message* looks like an SDK / JSON payload, not a sentence."""
    if not isinstance(message, str):
        return False
    text = message.strip()
    if not text:
        return False
    if text.lower().startswith("error:"):
        text = text[6:].strip()
    if _ERROR_CODE_DUMP.search(text):
        return True
    if _DICT_ERROR_KEY.search(text) and "{" in text and "}" in text:
        return True
    if len(text) >= 2 and text[0] in "{[" and text[-1] in "}]":
        return True
    return False


def user_visible_error_message(message: Optional[Any] = None) -> str:
    """Return a user-facing sentence. Never includes an ``Error:`` prefix."""
    if message is None:
        text = ""
    elif isinstance(message, str):
        text = message
    else:
        text = str(message)
    text = text.strip()
    if text.lower().startswith("error:"):
        text = text[6:].strip()
    if not text or looks_like_raw_provider_error(text):
        return PROVIDER_FAILURE_MESSAGE
    return text
