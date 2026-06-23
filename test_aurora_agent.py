#!/usr/bin/env python3
import subprocess
from src.providers import provider_manager; provider_manager.reload()
from src.main_flow import generate_chat_responses_stream_native

# Turn 1: agent must use tools — write a file
messages = [{"role": "user", "content": "Write a file called /workspace/hello.txt with content 'hello world'"}]
print("=== Turn 1 ===", flush=True)
events1 = list(generate_chat_responses_stream_native(
    messages, max_iterations=3, provider_id="holekv-qwen",
))
last = events1[-1]
print(f"status={last.get('status')}", flush=True)
cid1 = None
for ev in events1:
    c = ev.get("holekv_cache_id")
    if c: cid1 = c
print(f"T1 cache_id={cid1}", flush=True)

# Turn 2: edit the same file
msgs2 = list(last["messages"])
msgs2.append({"role": "user", "content": "Edit hello.txt to say 'hello holekv' instead"})
print("=== Turn 2 ===", flush=True)
events2 = list(generate_chat_responses_stream_native(
    msgs2, max_iterations=3, provider_id="holekv-qwen",
    holekv_cache_id=cid1,
))
last2 = events2[-1]
print(f"status={last2.get('status')}", flush=True)
cid2 = None
for ev in events2:
    c = ev.get("holekv_cache_id")
    if c: cid2 = c
print(f"T2 cache_id={cid2}", flush=True)

# Check imports
r = subprocess.run(["grep", "HoleKV imported", "/tmp/vllm_holekv.log"],
                   capture_output=True, text=True)
lines = [l for l in r.stdout.strip().split("\n") if l.strip()][-6:]
for l in lines:
    print(f"IMPORT: {l[-200:]}", flush=True)
