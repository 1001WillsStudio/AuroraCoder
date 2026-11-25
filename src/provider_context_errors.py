"""Recognize provider rejections where rebuilding the prompt can make progress."""

from collections.abc import Mapping


def is_context_limit_error(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) not in (400, 413):
        return False
    body = getattr(exc, "body", None)
    details = body.get("error", body) if isinstance(body, Mapping) else {}
    if not isinstance(details, Mapping):
        details = {}

    if "context_length_exceeded" in (getattr(exc, "code", None), details.get("code"), details.get("type")):
        return True

    # vLLM-compatible responses can provide a numeric HTTP code and a
    # structured input_tokens parameter or a plain context-length message.
    if details.get("param") in ("input_tokens", "prompt_tokens"):
        return True
    message = details.get("message")
    return isinstance(message, str) and message.lower().startswith("this model's maximum context length is ")
