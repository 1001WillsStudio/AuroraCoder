#!/usr/bin/env python3
"""Multi-turn HoleKV KV reuse test."""
import subprocess, sys
from src.providers import provider_manager
provider_manager.reload()
from src.main_flow import generate_chat_responses_stream_native

# Turn 1: write a file (triggers tools + panel refresh → markers injected)
print("=== Turn 1: write file ===", flush=True)
ev1 = list(generate_chat_responses_stream_native(
    [{"role": "user", "content": "write file /workspace/kv_test.txt with hello world"}],
    max_iterations=2, provider_id="holekv-qwen",
))
last1 = ev1[-1]
cid1 = last1.get("holekv_cache_id")
print(f"  status={last1.get('status')}  cid={cid1}", flush=True)

# Turn 2: edit same file (same conversation structure, different content)
msgs2 = list(last1["messages"])
msgs2.append({"role": "user", "content": "edit kv_test.txt to say goodbye world instead"})
print("\n=== Turn 2: edit file (with holekv_ref) ===", flush=True)
ev2 = list(generate_chat_responses_stream_native(
    msgs2, max_iterations=2, provider_id="holekv-qwen",
    holekv_cache_id=cid1,
))
last2 = ev2[-1]
cid2 = last2.get("holekv_cache_id")
print(f"  status={last2.get('status')}  cid={cid2}", flush=True)

# Check vLLM logs for KV import
r = subprocess.run(["grep", "HoleKV imported", "/tmp/vllm_holekv.log"],
                   capture_output=True, text=True)
imports = [l for l in r.stdout.strip().split("\n") if l.strip()][-4:]
print("\n=== vLLM import log ===", flush=True)
for l in imports:
    print(f"  {l[-180:]}", flush=True)

kv_ok = cid1 and any(cid1 in l for l in imports)
print(f"\nKV reuse confirmed: {kv_ok}", flush=True)
sys.exit(0 if kv_ok else 1)
