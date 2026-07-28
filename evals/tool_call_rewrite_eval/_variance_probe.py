"""Variance probe: do Arm B vs Arm A_clean diverge at T=0.7 on the [TO]-teaching
mode F3? If they diverge meaningfully, the teaching effect is measurable and we
scale up; if not, we reconsider the design/hypothesis."""
import json
from collections import Counter
from AuroraCoder.evals.tool_call_rewrite_eval import harness, llm as llm_mod, corpus

client = llm_mod.get_client()
trials = corpus.generate_trials(corpus.prepare_pristine(), 6, seed=1)
f3 = [t for t in trials if t["mode"] == "F3"]
print(f"F3 trials available: {len(f3)}; using {min(12, len(f3))}")
f3 = f3[:12]
T = 0.7
rows = []
for trial in f3:
    for arm in ("B", "A_clean"):
        r = harness.run_arm(trial, arm, client, model=llm_mod.DEFAULT_MODEL, temperature=T)
        rows.append(r)
        print(f"{r['trial_id']:28s} {r['arm']:7s} emit={r['edit_file_emitted']} "
              f"has_to={r.get('emitted_has_to')} templ_ok={r.get('template_structure_ok')} "
              f"used_right={r.get('used_right_template')} fmt={r.get('format_correct')}")
print("=" * 60)
for arm in ("B", "A_clean"):
    sub = [r for r in rows if r["arm"] == arm and r["edit_file_emitted"]]
    rate = sum(1 for r in sub if r.get("used_right_template")) / len(sub) if sub else 0
    has_to = sum(1 for r in sub if r.get("emitted_has_to"))
    print(f"arm={arm:7s} n_emit={len(sub)} used_right_template_rate={rate:.2f}  used_[TO]={has_to}/{len(sub)}")