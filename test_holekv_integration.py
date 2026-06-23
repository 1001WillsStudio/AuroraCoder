#!/usr/bin/env python3
"""AuroraCoder + HoleKV integration tests."""
import sys
from src.providers import provider_manager
provider_manager.reload()
from src.main_flow import generate_chat_responses_stream_native

ok = 0
fail = 0

def run_test(name, messages, max_iters=2, holekv_cache_id=None, expect_tools=False):
    global ok, fail
    print(f"=== {name} ===", flush=True)
    ev = list(generate_chat_responses_stream_native(
        messages, max_iterations=max_iters, provider_id="holekv-qwen",
        holekv_cache_id=holekv_cache_id,
    ))
    last = ev[-1]
    s = last.get("status")
    cid = last.get("holekv_cache_id")
    # Count tool calls in messages
    tool_msgs = sum(1 for m in last.get("messages", []) if m.get("role") == "tool")
    print(f"  status={s}  cid={cid}  tool_msgs={tool_msgs}", flush=True)
    if s in ("completed", "max_iterations_reached"):
        print(f"  PASS", flush=True)
        ok += 1
        return last
    else:
        print(f"  FAIL", flush=True)
        fail += 1
        return None

# Test 1: simple chat (no tools, no markers needed)
run_test("Test 1: simple chat",
    [{"role": "user", "content": "say hello in one word"}], max_iters=1)

# Test 2: write file (uses tools → panel refresh → markers injected)
run_test("Test 2: write file",
    [{"role": "user", "content": "write file /workspace/test_holekv_t2.txt with content ok"}], max_iters=2)

# Test 3: read file (uses tools)
run_test("Test 3: read file",
    [{"role": "user", "content": "read file /workspace/test_holekv_t2.txt"}], max_iters=1)

print(f"\n{'='*40}")
print(f"Passed: {ok}  Failed: {fail}")
sys.exit(0 if fail == 0 else 1)
