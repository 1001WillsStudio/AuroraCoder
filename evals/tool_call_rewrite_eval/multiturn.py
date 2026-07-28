"""Multi-turn trajectory harness: "4 tool calls where the first is by our design".

Trace shape per trial:
  T1 = SEED tool_call (designed by us:  Arm-A malformed, Arm-B canonical).
        Applied to a live file copy with the REAL engine. The seed is line-count-
        neutral so the post-seed file state is IDENTICAL across arms; B's history
        carries the canonical [TO] template, A's history carries the model's own
        no-[TO] full-block template. Both arms get a CLEAN success narrative
        (the engine accepts the no-[TO] full block via its has_to=False path, so
        there is NO auto-correct banner confound) and the same refreshed panel.
        So the ONLY difference across arms is the TEMPLATE of each prior assistant
        tool_call — the razor-clean isolation of the rewrite innovation.
  T2,T3,T4 = the AGENT's own follow-up edit_file calls, one per phase, each
        against a DISJOINT, line-count-neutral region so all phase prompts stay
        valid across the whole trajectory. We judge each emitted call's format.

Two arms (the innovation's core manipulation), held per turn:
  * B = WITH  the rewrite innovation: every applied call is rewritten in history
          to the engine's canonical *applied* form (which always uses [TO]
          for multi-line ranges) — exactly AuroraCoder production behavior.
  * A = WITHOUT the rewrite: the model's emitted call text is left verbatim in
          history even when the engine accepted+applied it. So if the model emits a
          no-[TO] full-block call that applies, A keeps that form while B flips it
          to [TO]. This is the compounding teaching signal under test.

Cost note: a trajectory is a SEQUENCE (turn k depends on turn k-1's applied
state), so it cannot be parallelised within a trajectory; trajectories are
parallelised across trials (run_multiturn_eval uses threads).
"""
import copy
import datetime
import json
import os
import pathlib
import tempfile
import shutil
import time

from . import _engine
from . import prompt_assets
from . import llm as llm_mod
from . import edits
from . import corpus

REMINDER = (
    "Reminder: the code interpreter display above shows the exact current line "
    "numbers of the file. Use those line numbers (do not rely on memory) and make "
    "this edit with a single edit_file call."
)


def _assistant_tool_call(name, arguments_dict, call_id):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments_dict, ensure_ascii=False)},
    }


def _nbytes(lines):
    return sum(len(l) + 1 for l in lines)


def _edit_tool_message(result_text, file_lines, relpath, call_id):
    panel = prompt_assets.render_panel(relpath, file_lines)
    return {"role": "tool", "tool_call_id": call_id, "name": "edit_file",
            "content": result_text + "\n\n" + panel}


def _extract_first_edit_call(tool_calls):
    for tc in tool_calls or []:
        if tc.get("function", {}).get("name") == "edit_file":
            return tc
    return None


# ---------------------------------------------------------------------------
# Disjoint multi-line phase picker (line-count-neutral regions)
# ---------------------------------------------------------------------------
def _disjoint_multi_regions(lines, n_needed, min_gap=6, used=None, rng=None):
    """Return up to n_needed disjoint multi-line blocks (len 2..5, anchor lines
    unique+identifier-bearing). Regions are pairwise non-overlapping with at least
    `min_gap` lines between them, and avoid any line in `used`. If `rng` is given,
    candidate block-starts are shuffled so different (file, rep) trials can each
    get their own disjoint region set. Used to pick the seed region + phase
    regions so every precomputed phase prompt stays valid across the trajectory."""
    used = used or set()
    sing_map = dict(corpus.good_single_lines(lines))
    starts = sorted(sing_map)
    if rng is not None:
        rng.shuffle(starts)
    chosen = []
    for L in starts:
        for k in range(1, 5):          # block length 2..5
            Lk = L + k
            if Lk not in sing_map:
                break
            # disjoint + gap from already chosen regions
            ok = True
            for c in chosen:
                if not (Lk + min_gap < c["start"] or L > c["end"] + min_gap):
                    ok = False
                    break
            if not ok:
                continue
            # disjoint + gap from `used` lines
            if any(abs(line - u) <= min_gap for line in range(L, Lk + 1) for u in used):
                continue
            chosen.append({"start": L, "end": Lk, "first": sing_map[L],
                           "last": sing_map[Lk],
                           "block_lines": [edits.normalize_line(x) for x in lines[L - 1: Lk]]})
            break  # advance to next seed start once this one is consumed
        if len(chosen) >= n_needed:
            break
    return chosen[:n_needed]


def _line_neutral_replace(block_lines):
    """Make a line-count-NEUTRAL replacement for a block: rename the first
    identifier found on the LAST line, keep line count identical."""
    bl = list(block_lines)
    tok = edits.first_identifier(bl[-1])
    if not tok:
        return None, None
    new = tok + "zz"
    bl[-1] = bl[-1].replace(tok, new, 1)
    return "\n".join(bl), tok


# ---------------------------------------------------------------------------
# Trial factory: seed + N phase instructions, each line-neutral & disjoint
# ---------------------------------------------------------------------------
def make_trajectory_trial(src_file, relpath, n_phases=3, seed_mode="F3", rep=0, rng_seed=0):
    """Build a multi-turn trial with a designed seed (call 1) and n_phases agent
    edits (calls 2..n+1). All regions disjoint + line-count-neutral so precomputed
    phase prompts stay valid across the whole trajectory. `rep` (with `rng_seed`)
    shuffles candidate block-starts so multiple trajectory trials per file can
    each pick a different disjoint region set.

    Returns dict or None if the file can't support (seed + n_phases) regions or the
    seed-mode malformed doesn't behave per its narrative.
    """
    import random
    rng = random.Random(rng_seed + rep * 9973)

    lines = pathlib.Path(src_file).read_text().splitlines()
    sing = corpus.good_single_lines(lines)
    if not sing:
        return None
    # Pick disjoint regions: 1 for seed + n_phases for agent.
    regions = _disjoint_multi_regions(lines, 1 + n_phases, rng=rng)
    if len(regions) < 1 + n_phases:
        return None
    seed_region = regions[0]
    phase_regions = regions[1:]
    # sanity: all must admit a line-neutral rename replacement on last line
    for r in [seed_region] + phase_regions:
        repl, tok = _line_neutral_replace(r["block_lines"])
        if repl is None:
            return None

    # ---- build the SEED canonical + malformed ----
    sm_repl, sm_tok = _line_neutral_replace(seed_region["block_lines"])
    canonical_seed = edits.canonical_multi(relpath, seed_region["start"], seed_region["end"],
                                           seed_region["first"], seed_region["last"], sm_repl)
    full_block = "\n".join(seed_region["block_lines"])
    malformed_seed = {"file": relpath, "edits": [{
        "remove_line_number": f"{seed_region['start']}-{seed_region['end']}",
        "content_to_remove": full_block,       # NO [TO] — template-violating form
        "replace_content": sm_repl,
    }]}
    # ---- verify BOTH seed forms apply and produce IDENTICAL resulting file ----
    # (sm_repl is the same replacement for both, so the post-seed file state is
    #  identical across arms; only the assistant tool_call TEMPLATE differs:
    #  B uses the canonical [TO] form, A uses the no-[TO] full-block form.) The
    #  engine accepts the no-[TO] full block via its has_to=False path, so the
    #  narrative text is a clean success for both — NO banner confound here.
    succ_text, applied, post_lines, post_text = _engine.run_edit_on_copy(
        src_file, canonical_seed, str(pathlib.Path(src_file).parent))
    if applied is None:
        return None
    mal_text, mal_applied, mal_post, _ = _engine.run_edit_on_copy(
        src_file, malformed_seed, str(pathlib.Path(src_file).parent))
    if mal_applied is None:           # malformed seed must apply (so file state matches)
        return None
    # require the two seeds land on the SAME resulting file (same newline-stripped text)
    if [edits.normalize_line(x) for x in post_lines] != [edits.normalize_line(x) for x in mal_post]:
        return None
    if len(post_lines) != len(lines):
        return None

    # ---- build PHASE prompts (against the post-seed file) ----
    # Region line numbers are unchanged by the seed (seed is line-neutral), so
    # phase_region line ranges refer correctly in the post-seed file too. Verify:
    for pr in phase_regions:
        if pr["first"] != edits.normalize_line(post_lines[pr["start"] - 1]):
            return None
    phases = []
    for i, pr in enumerate(phase_regions):
        repl, tok = _line_neutral_replace(pr["block_lines"])
        new = (edits.first_identifier(pr["last"]) or "x") + "zz"
        # oracle canonical call for this phase (what a "perfect" edit looks like)
        oracle = edits.canonical_multi(relpath, pr["start"], pr["end"],
                                       pr["first"], pr["last"], repl)
        # phase instruction: NEUTRAL about how to format content_to_remove
        new_block = "\n".join(pr["block_lines"])
        new_block = new_block.replace(pr["last"], repl, 1) if pr["last"] in new_block else new_block
        instr = (
            f"Now make another small edit to the SAME file (`{relpath}`). "
            f"Replace the lines from line {pr['start']} through line {pr['end']} "
            f"with the following content:\n\n```\n{repl}\n```\n\n"
            f"Use a single edit_file call."
        )
        phases.append({"phase_index": i, "region": {"start": pr["start"], "end": pr["end"]},
                       "instruction": instr, "oracle": oracle, "oracle_range": (pr["start"], pr["end"]),
                       "oracle_first": pr["first"], "oracle_last": pr["last"]})

    return {
        "trial_id": f"{pathlib.Path(src_file).stem}__TRJ__{seed_mode}__r{rep}",
        "relpath": relpath,
        "seed_mode": seed_mode,
        "size": len(lines),
        "pristine_lines": lines,
        "post_seed_lines": post_lines,
        "seed_region": {"start": seed_region["start"], "end": seed_region["end"]},
        "seed_canonical": canonical_seed,
        "seed_malformed": malformed_seed,
        "seed_malformed_result_text": mal_text,    # genuine production text (clean success; no banner)
        "seed_canonical_result_text": succ_text,
        "phases": phases,
    }


# ---------------------------------------------------------------------------
# Per-turn judge
# ---------------------------------------------------------------------------
def judge_turn(emitted_raw_args, live_lines):
    return _engine.judge_probe_call(emitted_raw_args, live_lines)


# ---------------------------------------------------------------------------
# Live file state (applied-per-turn) in a temp workspace
# ---------------------------------------------------------------------------
class LiveFile:
    def __init__(self, src_file, relpath):
        self.ws = pathlib.Path(tempfile.mkdtemp(prefix="ac_traj_"))
        self.relpath = relpath
        dst = self.ws / pathlib.Path(relpath).name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst)
        self.path = dst
        self.editor = _engine.RangeReplaceEditor(str(self.ws))

    def apply_edit(self, args_dict):
        # args_dict: {"file":.., "edits":[...]} ; run against live file
        args = copy.deepcopy(args_dict)
        args["file"] = pathlib.Path(self.relpath).name
        result, applied = self.editor.edit(args["file"], args.get("edits", []))
        return result, applied

    def lines(self):
        return self.path.read_text().splitlines()

    def close(self):
        shutil.rmtree(self.ws, ignore_errors=True)


# ---------------------------------------------------------------------------
# Trajectory runner
# ---------------------------------------------------------------------------
def run_trajectory(trial, arm, client, *, model, temperature, n_phases=3):
    """Run one (trial, arm) multi-turn trajectory. Returns a list of per-turn
    row dicts (T1=seed judged-trivially + T2..T{n+1}=agent calls)."""
    assert arm in ("B", "A")
    src_file = corpus.PRISTINE.parent / trial["relpath"]
    rel = trial["relpath"]

    out_rows = []
    live = LiveFile(src_file, rel)
    live_lines_start = live.lines()
    try:
        # ---- build messages up to T1 (seed) ----
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        tree = prompt_assets.build_workspace_tree([rel])
        system = prompt_assets.build_system_message(tree, current_time=now)
        msgs = [{"role": "system", "content": system}]

        msgs.append({"role": "user", "content": f"Read the file `{rel}` and study its current content."})
        msgs.append({"role": "assistant", "content": "",
                     "tool_calls": [_assistant_tool_call("read_file", {"file": rel}, "call_read")]})
        post_seed = trial["post_seed_lines"]
        msgs.append({"role": "tool", "tool_call_id": "call_read", "name": "read_file",
                     "content": prompt_assets.read_file_notice(rel, post_seed, _nbytes(post_seed))})

        # apply the SEED to the live file (T1) using the genuine production path
        if arm == "B":
            seed_args = trial["seed_canonical"]
            seed_result_text = trial["seed_canonical_result_text"]   # clean
            # B's history assistant call is already canonical (the applied form);
            # we leave it as-is (= canonical). Rewrite is a no-op here because
            # canonical IS the applied form.
        else:  # A
            seed_args = trial["seed_malformed"]
            seed_result_text = trial["seed_malformed_result_text"]   # clean success (genuine; no banner)
        # apply to live file
        live_res, live_applied = live.apply_edit(seed_args)
        if live_applied is None:
            # Tool actually failed against the live pristine copy too — for F3 it
            # should apply; bail but record.
            out_rows.append({"trial_id": trial["trial_id"], "arm": arm, "turn": 1,
                             "role": "seed", "emit_error": "seed_did_not_apply_live",
                             "temperature": temperature, "model": model})
            return out_rows
        # T1 judge: the EMITTED seed template (B canonical=1, A malformed=0)
        seed_emitted_template_ok = True if arm == "B" else (
            judge_turn(json.dumps(seed_args, ensure_ascii=False), live.lines()).get("template_correct", False)
        )
        out_rows.append({
            "trial_id": trial["trial_id"], "arm": arm, "turn": 1, "role": "seed",
            "temperature": temperature, "model": model,
            "edit_file_emitted": True,
            "seed_template_correct_by_design": seed_emitted_template_ok,
            "emit_applied": True,
            # the headline trace metric for T1 is constant by construction:
            "used_right_template": seed_emitted_template_ok,
            "emitted_has_to": ("\n[TO]\n" in str(seed_args["edits"][0]["content_to_remove"])),
        })

        # append T1 assistant message (B rewrites to canonical applied form; A leaves emitted)
        if arm == "B":
            # canonical applied form is the engine's normalized applied edit
            hist_seed = trial["seed_canonical"]
        else:
            hist_seed = trial["seed_malformed"]
        msgs.append({"role": "assistant", "content": "",
                     "tool_calls": [_assistant_tool_call("edit_file", hist_seed, "call_seed_end")]})
        msgs.append(_edit_tool_message(seed_result_text, live.lines(), rel, "call_seed_end"))

        # ---- T2..T{n+1}: agent phases ----
        for pi, phase in enumerate(trial["phases"][:n_phases]):
            call_id = f"call_p{pi}"
            msgs.append({"role": "user", "content": phase["instruction"] + "\n\n" + REMINDER})
            # transient upstream errors are common (DeepSeek Go Console 400s);
            # retry the same phase a few times with backoff before giving up.
            resp, err = None, None
            for _attempt in range(4):
                resp, _elapsed, err = llm_mod.call_once(client, msgs, prompt_assets.NATIVE_TOOLS,
                                                       model=model, temperature=temperature,
                                                       retries=2, timeout=150)
                if not err and resp is not None:
                    break
                if _attempt < 3:
                    time.sleep(3.0 * (_attempt + 1))
            ec = _extract_first_edit_call(resp.get("tool_calls") if resp else None) if not err else None
            row = {
                "trial_id": trial["trial_id"], "arm": arm, "turn": pi + 2, "role": "agent",
                "phase_index": pi, "temperature": temperature, "model": model,
                "llm_error": err, "finish_reason": (resp or {}).get("finish_reason"),
                "tools_called": ([tc["function"]["name"] for tc in resp.get("tool_calls")] if resp and resp.get("tool_calls") else None),
                "edit_file_emitted": False,
                "emitted_edit_file_raw_args": None,
                "emit_applied": False, "emit_result_text": None,
                "used_right_template": None, "template_correct": None,
                "anchor_resolvable_before_apply": None, "emitted_has_to": None,
                "emitted_remove_line_number": None, "violations": None,
                "oracle_range": phase["oracle_range"],
                "wall_elapsed_phase": None,
            }
            if err or ec is None:
                # we still need to append *something* to history to continue the
                # trajectory. If the model emitted another tool, apply THAT one so
                # the file/state can evolve; if no tool, skip apply (file unchanged)
                # but we cannot continue cleanly — record + break.
                other = None
                if resp and resp.get("tool_calls"):
                    other = resp["tool_calls"][0]
                if other and other["function"]["name"] in ("edit_file",):
                    ec = other
                else:
                    out_rows.append(row)
                    break
            row["edit_file_emitted"] = True
            raw_args = ec["function"]["arguments"]
            row["emitted_edit_file_raw_args"] = raw_args

            # judge emitted BEFORE apply (format of what the model emitted)
            try:
                emitted = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except Exception:
                emitted = None
            if emitted and isinstance(emitted, dict) and emitted.get("edits"):
                row["emitted_has_to"] = "\n[TO]\n" in str(emitted["edits"][0].get("content_to_remove", ""))
                row["emitted_remove_line_number"] = emitted["edits"][0].get("remove_line_number")
            pre_judge = judge_turn(raw_args, live.lines())
            row["used_right_template"] = pre_judge.get("used_right_template")
            row["template_correct"] = pre_judge.get("template_correct")
            row["anchor_resolvable_before_apply"] = pre_judge.get("anchor_resolvable")
            row["violations"] = pre_judge.get("violations")

            # APPLY the emitted edit to the live file (real production engine:
            # auto-corrects anchor/line/indent as needed). Use the emitted args
            # dict (re-based) so the engine resolves against current state.
            emit_args = emitted if isinstance(emitted, dict) else {}
            try:
                emit_res, emit_applied = live.apply_edit(emit_args if emit_args else
                                                         {"file": rel, "edits": []})
            except Exception as exc:
                emit_res = f"apply_exception:{exc!r}"
                emit_applied = None
            row["emit_applied"] = (emit_applied is not None)
            row["emit_result_text"] = emit_res

            # ---- append turn to history (THE INNOVATION FORK) ----
            if arm == "B":
                if emit_applied is not None:
                    # WITH rewrite: replace the assistant call args with the
                    # engine's canonical *applied* form (production behavior).
                    hist_args = copy.deepcopy(emit_applied)
                    hist_args["file"] = rel
                else:
                    # B + error: production leaves the call as-emitted; file
                    # unchanged (the rewrite only fires on success-after-fix).
                    hist_args = emit_args if emit_args else {"file": rel, "edits": []}
                hist_tool_text = emit_res       # genuine production text
            else:  # arm == A (WITHOUT rewrite): leave emitted args verbatim always
                hist_args = emit_args if emit_args else {"file": rel, "edits": []}
                hist_tool_text = emit_res       # genuine production text (banner/error)

            msgs.append({"role": "assistant", "content": "",
                         "tool_calls": [_assistant_tool_call("edit_file", hist_args, call_id)]})
            msgs.append(_edit_tool_message(hist_tool_text, live.lines(), rel, call_id))
            out_rows.append(row)

        return out_rows
    finally:
        live.close()