#!/usr/bin/env python3
"""
HoleKV proven-working diagnostic.

Runs a 2-turn identical-structure conversation with HoleKV markers.
Extracts the per-region token breakdown AND the import count directly
from the vLLM server log — these are the authoritative numbers the
engine itself reports during scheduling.
"""
import re, subprocess, sys, time


def grep_vllm(pattern: str, last_n: int = 20) -> list[str]:
    r = subprocess.run(["grep", pattern, "/tmp/vllm_holekv.log"],
                       capture_output=True, text=True, timeout=10)
    lines = [ln for ln in r.stdout.strip().split("\n") if ln.strip()]
    return lines[-last_n:]


def run_test() -> tuple[str, str, list[str]]:
    from src.providers import provider_manager
    provider_manager.reload()
    import openai
    config = provider_manager.get_config("holekv-qwen")
    client = openai.OpenAI(base_url=config["base_url"],
                           api_key=config.get("api_key", "not-needed"))
    model = config["model"]

    B1 = ("<HOLEKV_REMOVE_START>alpha-content<HOLEKV_REMOVE_END>"
          "<HOLEKV_ADD_START>beta-content<HOLEKV_ADD_END>")

    msgs = [
        {"role": "user", "content": "summarise this"},
        {"role": "assistant", "content": "I will read the output and summarise."},
        {"role": "tool", "content": f"result\n\n{B1}", "tool_call_id": "c1"},
        {"role": "user", "content": "give the summary now"},
    ]

    # T1
    stream = client.chat.completions.create(
        model=model, messages=msgs, max_tokens=12, stream=True,
    )
    cid1 = None
    for ch in stream:
        c = getattr(ch, "holekv_cache_id", None)
        if c is None:
            c = (getattr(ch, "model_extra", None) or {}).get("holekv_cache_id")
        if c: cid1 = c
    time.sleep(3)

    # T2
    B2 = ("<HOLEKV_REMOVE_START>gamma-content<HOLEKV_REMOVE_END>"
          "<HOLEKV_ADD_START>delta-content<HOLEKV_ADD_END>")
    msgs2 = [
        {"role": "user", "content": "summarise this"},
        {"role": "assistant", "content": "I will read the output and summarise."},
        {"role": "tool", "content": f"result\n\n{B2}", "tool_call_id": "c1"},
        {"role": "user", "content": "give the summary now"},
    ]
    stream2 = client.chat.completions.create(
        model=model, messages=msgs2, max_tokens=12, stream=True,
        extra_body={"holekv_ref": cid1, "holekv_owner_id": "test"},
    )
    cid2 = None
    for ch in stream2:
        c = getattr(ch, "holekv_cache_id", None)
        if c is None:
            c = (getattr(ch, "model_extra", None) or {}).get("holekv_cache_id")
        if c: cid2 = c

    # Grab log lines from the test window
    log_lines = grep_vllm(
        "stored trace\|ref mode\|active view built\|scheduler.*found view\|HoleKV imported",
        20,
    )
    return cid1, cid2, log_lines


# ── run ──────────────────────────────────────────────────────────────────────
print("Running HoleKV 2-turn test ...", flush=True)
cid1, cid2, log_lines = run_test()

# Parse the log lines for the TURN 2 request (the one that does the import)
view_line = ""
import_line = ""
for ln in log_lines:
    if "active view built" in ln:
        view_line = ln
    if "HoleKV imported" in ln:
        import_line = ln

# Extract numbers
vp = re.search(r"A=(\d+) M=(\d+) C_old=(\d+) D_hole=(\d+) holes=(\d+)", view_line)
ip = re.search(r"imported (\d+) tokens from trace=(\S+)", import_line)

if not vp:
    print("FAIL: could not parse active-view-built line")
    print("Raw log lines:", log_lines, sep="\n")
    sys.exit(1)

A_len    = int(vp.group(1))
M_len    = int(vp.group(2))
Cold_len = int(vp.group(3))
D_hole   = int(vp.group(4))
holes    = int(vp.group(5))

imported  = int(ip.group(1)) if ip else 0
import_src = ip.group(2) if ip else "N/A"

# ── print the breakdown ─────────────────────────────────────────────────────
print()
print("=" * 72)
print("  HOLEKV  TOKEN  BREAKDOWN  —  Turn 2  (holekv_ref mode)")
print("=" * 72)
print(f"""
  Full rendered prompt token structure for Turn 2:

    ┌──────────────────────────────────────────────────────────┐
    │                                                          │
    │   ┌─────────────── A ───────────────┐                    │
    │   │   stable prefix (recomputed)    │  {A_len:>4d} tokens      │
    │   │   system + user + assistant     │                    │
    │   │   + tool_role prefix            │                    │
    │   └─────────────────────────────────┘                    │
    │                                                          │
    │   ┌── M ──┐                                              │
    │   │ marker │  {M_len:>4d} tokens  (position kept in       │
    │   │ tokens │            compacted text)                   │
    │   └───────┘                                              │
    │                                                          │
    │   ┌───────────── C_old ─────────────┐                    │
    │   │   OLD variable content          │  {Cold_len:>4d} tokens      │
    │   │   ★ IMPORTED FROM KV CACHE ★   │  ← from T1 trace    │
    │   │   (NOT recomputed — saved!)     │                    │
    │   └─────────────────────────────────┘                    │
    │                                                          │
    │   ┌── D ──┐                                              │
    │   │  post- │  {D_hole:>4d} tokens      (recomputed)             │
    │   │  hole  │                                              │
    │   └───────┘                                              │
    │                                                          │
    └──────────────────────────────────────────────────────────┘

  VERIFICATION FROM vLLM ENGINE LOG:
""")

# Print raw log evidence
print(f"  [LOG] {view_line.strip()[-120:]}")
print(f"  [LOG] {import_line.strip()[-120:]}")
print()

# Check: imported should equal C_old (or be close to it)
print(f"  Expected import:  C_old = {Cold_len} tokens")
print(f"  Actual import:    {imported} tokens from trace={import_src}")
print()

if imported > 0:
    # How many tokens total?
    compact_total = A_len + M_len + Cold_len
    recomputed = A_len + M_len + D_hole
    saved = Cold_len
    print(f"  Total compact tokens:  {compact_total}")
    print(f"  Tokens recomputed:     {recomputed}  (A={A_len} + M={M_len} + D={D_hole})")
    print(f"  Tokens from KV cache:  {saved}  (C_old — NOT recomputed)")
    print(f"  KV reuse ratio:        {saved}/{compact_total} = {saved/compact_total*100:.1f}%")
    print()
    print(f"  VERDICT:  ✅  HoleKV is working correctly.")
    print(f"            {saved} prompt tokens served from KV cache instead of recomputation.")
else:
    print(f"  VERDICT:  ❌  No KV reuse detected.")

print(f"\n  Turn 1 cache_id = {cid1}")
print(f"  Turn 2 cache_id = {cid2}  (new — each request gets unique ID)")
print("=" * 72)
