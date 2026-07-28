"""Smoke test: run 2 trials (one F3, one error mode) across all 3 arms against the
real LLM to validate the harness + judge behave end-to-end."""
import json
import os
import sys

from AuroraCoder.evals.tool_call_rewrite_eval import harness, llm as llm_mod, corpus

client = llm_mod.get_client()  # uses OPENCODE_API_KEY env
trials = corpus.generate_trials(corpus.prepare_pristine(), 3, seed=0)
# pick one apply (F3) and one error (F2)
f3 = next(t for t in trials if t["mode"] == "F3")
f2 = next(t for t in trials if t["mode"] == "F2")
for trial in (f3, f2):
    print("=" * 60)
    print("trial", trial["trial_id"], "mode", trial["mode"], "narr", trial["narrative"])
    print("probe:", trial["probe_instruction"][:120].replace("\n", " "))
    print("canonical ctr:", repr(trial["seed_canonical"]["edits"][0]["content_to_remove"][:70]))
    print("malformed ctr:", repr(trial["seed_malformed"]["edits"][0]["content_to_remove"][:70]))
    for arm in ("B", "A_clean", "A_nat"):
        row = harness.run_arm(trial, arm, client, model=llm_mod.DEFAULT_MODEL, temperature=0.0)
        print(f"  [{arm}] emit={row['edit_file_emitted']} first_tool={row['first_tool_name']} "
              f"has_to={row.get('emitted_has_to')} rln={row.get('emitted_remove_line_number')} "
              f"templ_ok={row.get('template_structure_ok')} anchor={row.get('anchor_resolvable')} "
              f"used_right={row.get('used_right_template')} fmt={row.get('format_correct')} "
              f"err={row.get('llm_error')}")
        if row.get("violations"):
            print("       violations:", row["violations"])
        if row.get("emitted_edit_file_raw_args"):
            print("       args:", row["emitted_edit_file_raw_args"][:200].replace("\n", "\\n"))