"""OpenAI-compatible LLM client for the eval.

Talks to the OpenCode router (DeepSeek V4 Pro). The harness makes exactly ONE
non-streaming chat.completions call per arm and captures the assistant message
(we measure the *next* emitted edit_file call, not a multi-turn trajectory).
"""
import json
import os
import time

from openai import OpenAI

DEFAULT_BASE_URL = "https://opencode.ai/zen/go/v1"
DEFAULT_MODEL = "deepseek-v4-pro"


def get_client(api_key=None, base_url=None):
    api_key = api_key or os.environ.get("OPENCODE_API_KEY")
    base_url = base_url or os.environ.get("OPENCODE_BASE_URL", DEFAULT_BASE_URL)
    if not api_key:
        raise RuntimeError("OPENCODE_API_KEY env var (or api_key arg) not set")
    return OpenAI(api_key=api_key, base_url=base_url)


def call_once(client, messages, tools, *, model=DEFAULT_MODEL, temperature=0.0,
              max_tokens=2048, timeout=120, retries=2):
    """Single chat.completions call. Returns (raw_assistant_message_dict, elapsed_s, error_or_None)."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            t0 = time.time()
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            elapsed = time.time() - t0
            msg = resp.choices[0].message
            # serialise to plain dict
            out = {
                "content": msg.content,
                "tool_calls": None,
            }
            if msg.tool_calls:
                out["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            out["finish_reason"] = resp.choices[0].finish_reason
            return out, elapsed, None
        except Exception as exc:
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    return None, 0.0, f"{type(last_err).__name__}: {last_err}"