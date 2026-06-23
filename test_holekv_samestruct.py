#!/usr/bin/env python3
"""
HoleKV same-structure multi-turn test with markers.
Both turns have identical message arrays, only the marker content differs.
"""
import subprocess, sys, time

from src.providers import provider_manager
provider_manager.reload()
import openai
config = provider_manager.get_config("holekv-qwen")
c = openai.OpenAI(base_url=config["base_url"], api_key=config.get("api_key", "not-needed"))
model = config["model"]

B1 = "<HOLEKV_REMOVE_START>old<HOLEKV_REMOVE_END><HOLEKV_ADD_START>new<HOLEKV_ADD_END>"
B2 = "<HOLEKV_REMOVE_START>replaced<HOLEKV_REMOVE_END><HOLEKV_ADD_START>updated<HOLEKV_ADD_END>"

msgs = [
    {"role": "user", "content": "analyse this"},
    {"role": "assistant", "content": "I will process the tool output."},
    {"role": "tool", "content": f"data\n\n{B1}", "tool_call_id": "c1"},
    {"role": "user", "content": "give result"},
]

# Turn 1
print("=== T1 ===", flush=True)
s = c.chat.completions.create(model=model, messages=msgs, max_tokens=16, stream=True)
cid1 = None
for ch in s:
    v = getattr(ch, "holekv_cache_id", None)
    if v is None: v = (getattr(ch, "model_extra", None) or {}).get("holekv_cache_id")
    if v: cid1 = v
print(f"cid1={cid1}", flush=True)
time.sleep(3)

# Turn 2
msgs2 = [
    {"role": "user", "content": "analyse this"},
    {"role": "assistant", "content": "I will process the tool output."},
    {"role": "tool", "content": f"data\n\n{B2}", "tool_call_id": "c1"},
    {"role": "user", "content": "give result"},
]
print("=== T2 ===", flush=True)
s2 = c.chat.completions.create(model=model, messages=msgs2, max_tokens=16, stream=True,
    extra_body={"holekv_ref": cid1, "holekv_owner_id": "test"})
cid2 = None
for ch in s2:
    v = getattr(ch, "holekv_cache_id", None)
    if v is None: v = (getattr(ch, "model_extra", None) or {}).get("holekv_cache_id")
    if v: cid2 = v
print(f"cid2={cid2}", flush=True)

# Verify
r = subprocess.run(["grep", f"HoleKV imported.*{cid1}", "/tmp/vllm_holekv.log"],
                   capture_output=True, text=True)
if r.stdout.strip():
    print(f"\nKV REUSE: {r.stdout.strip()[-200:]}", flush=True)
    sys.exit(0)
else:
    # Check ref mode at least
    r2 = subprocess.run(["grep", f"ref mode.*{cid1}", "/tmp/vllm_holekv.log"],
                        capture_output=True, text=True)
    if r2.stdout.strip():
        print(f"\nREF MODE OK: {r2.stdout.strip()[-200:]}", flush=True)
    sys.exit(1)
