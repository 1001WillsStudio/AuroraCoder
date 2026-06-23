#!/usr/bin/env python3
"""
Proper multi-turn HoleKV end-to-end test.

Mirrors the actual AuroraCoder flow:
  - Same conversation structure across turns (messages array is identical)
  - HoleKV markers injected into tool-message content (simulating panel display)
  - Turn 1 stores a trace; Turn 2 reuses it via holekv_ref

Verifies:
  1. Turn 1 returns a holekv_cache_id
  2. Turn 2 finds the trace, alignment succeeds, blocks are imported
  3. vLLM log confirms "HoleKV imported N tokens from trace=X"
"""

import subprocess
import sys
import time

all_ok = True


def _extract_cache_id(chunk) -> str | None:
    cid = getattr(chunk, "holekv_cache_id", None)
    if cid is not None:
        return cid
    extra = getattr(chunk, "model_extra", None)
    if extra:
        return extra.get("holekv_cache_id")
    return None


def _verdict(ok: bool, msg: str) -> None:
    global all_ok
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {msg}")
    if not ok:
        all_ok = False


def run() -> bool:
    global all_ok
    from src.providers import provider_manager
    provider_manager.reload()

    import openai
    config = provider_manager.get_config("holekv-qwen")
    client = openai.OpenAI(
        base_url=config["base_url"],
        api_key=config.get("api_key", "not-needed"),
    )
    model = config["model"]

    # ── Shared message template ────────────────────────────────────────
    B1 = (
        "<HOLEKV_REMOVE_START>old-tool-output"
        "<HOLEKV_REMOVE_END>"
        "<HOLEKV_ADD_START>new-tool-output"
        "<HOLEKV_ADD_END>"
    )

    messages_t1 = [
        {"role": "user", "content": "summarise this"},
        {"role": "assistant", "content": "I'll read the tool output."},
        {"role": "tool", "content": f"tool result\n\n{B1}", "tool_call_id": "call_1"},
        {"role": "user", "content": "now give the final answer"},
    ]

    # ── Turn 1 ─────────────────────────────────────────────────────────
    print("── Turn 1 · store trace ──")
    t1_cache_id: str | None = None
    stream = client.chat.completions.create(
        model=model, messages=messages_t1, max_tokens=16, stream=True,
    )
    for chunk in stream:
        cid = _extract_cache_id(chunk)
        if cid is not None:
            t1_cache_id = cid

    print(f"  cache_id = {t1_cache_id}")
    _verdict(t1_cache_id is not None, "Turn 1 returned a holekv_cache_id")

    print("\n  (waiting for Turn 1 to finish & trace to be stored...)")
    time.sleep(3)

    # ── Turn 2 · reuse trace ───────────────────────────────────────────
    print("\n── Turn 2 · reuse trace via holekv_ref ──")
    B2 = (
        "<HOLEKV_REMOVE_START>different-output"
        "<HOLEKV_REMOVE_END>"
        "<HOLEKV_ADD_START>different-output"
        "<HOLEKV_ADD_END>"
    )

    messages_t2 = [
        {"role": "user", "content": "summarise this"},
        {"role": "assistant", "content": "I'll read the tool output."},
        {"role": "tool", "content": f"tool result\n\n{B2}", "tool_call_id": "call_1"},
        {"role": "user", "content": "now give the final answer"},
    ]

    t2_cache_id: str | None = None
    stream2 = client.chat.completions.create(
        model=model, messages=messages_t2, max_tokens=16, stream=True,
        extra_body={"holekv_ref": t1_cache_id, "holekv_owner_id": "test_owner"},
    )
    for chunk in stream2:
        cid = _extract_cache_id(chunk)
        if cid is not None:
            t2_cache_id = cid

    print(f"  cache_id = {t2_cache_id}")
    _verdict(t2_cache_id is not None, "Turn 2 returned a holekv_cache_id")

    # ── Verify KV reuse via server log ─────────────────────────────────
    print("\n── Checking vLLM log for import confirmation ──")
    try:
        log_grep = subprocess.run(
            ["grep", "HoleKV imported", "/tmp/vllm_holekv.log"],
            capture_output=True, text=True, timeout=10,
        )
        import_lines = [ln for ln in log_grep.stdout.strip().split("\n") if ln.strip()]
        recent = import_lines[-3:]
        for ln in recent:
            print(f"  log: {ln[-160:]}")

        if t1_cache_id and any(t1_cache_id in ln for ln in recent):
            _verdict(True, "Server log confirms KV reuse (trace import)")
        else:
            _verdict(False, "Server log does NOT confirm KV reuse")
    except Exception as e:
        print(f"  [WARN] could not check vLLM log: {e}")

    # ── Summary ────────────────────────────────────────────────────────
    print(f"\n{'═' * 50}")
    if all_ok:
        print("ALL CHECKS PASSED — HoleKV multi-turn KV reuse works")
    else:
        print("SOME CHECKS FAILED — see details above")
    return all_ok


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
