"""Harness: assemble the seeded message history for one (trial, arm), make ONE LLM
call, then deterministically judge the emitted edit_file probe call.

Arms (manipulating only the history of the *prior* edit_file tool_call):
  * B       — canonical (rewritten-to-template) prior call + clean success text
              + post-edit file panel.  (the real AuroraCoder "rewrite" path)
  * A_clean — malformed prior call + the SAME clean success text + the SAME
              post-edit panel as B. ONLY the assistant tool_call text differs.
              -> razor-clean isolation of the call-text effect (PRIMARY).
  * A_nat   — malformed prior call + the genuine natural result text + the file
              state the model would actually see in production (post-edit for
              apply modes; pristine/unchanged for error modes).
              -> natural production behaviour (confounded: call-text + narrative
              + file state). Reported as SECONDARY.

The probe task (next edit) is identical across arms. We measure the format of the
model's emitted edit_file call (primary metric: `used_right_template`).
"""
import datetime
import json
import time

from . import _engine
from . import prompt_assets
from . import llm as llm_mod

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


def _edit_tool_message(result_text, file_lines, relpath):
    panel = prompt_assets.render_panel(relpath, file_lines)
    return {"role": "tool", "tool_call_id": "call_edit", "name": "edit_file",
            "content": result_text + "\n\n" + panel}


def build_messages(trial, arm):
    """Build the seeded chat history for (trial, arm)."""
    rel = trial["relpath"]
    post_lines = trial["post_lines"]
    a_nat_lines = trial["a_nat_file_lines"]
    a_nat_text = trial["a_nat_result_text"]
    succ_text = trial["seed_success_text"]

    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    tree = prompt_assets.build_workspace_tree([rel])
    system = prompt_assets.build_system_message(tree, current_time=now)

    msgs = [{"role": "system", "content": system}]

    # user: read the file
    msgs.append({"role": "user", "content":
                 f"Read the file `{rel}` and study its current content."})

    # assistant read_file call
    msgs.append({"role": "assistant", "content": "",
                 "tool_calls": [_assistant_tool_call("read_file", {"file": rel}, "call_read")]})

    # tool read notice (panel stripped as production does after the later edit trigger)
    read_notice = prompt_assets.read_file_notice(rel, post_lines, _nbytes(post_lines))
    msgs.append({"role": "tool", "tool_call_id": "call_read", "name": "read_file",
                 "content": read_notice})

    # assistant edit_file call (THE manipulated variable)
    if arm == "B":
        seed_args = trial["seed_canonical"]
    else:  # A_clean or A_nat both use the malformed call
        seed_args = trial["seed_malformed"]
    msgs.append({"role": "assistant", "content": "",
                 "tool_calls": [_assistant_tool_call("edit_file", seed_args, "call_edit")]})

    # tool result: text + panel depend on arm
    if arm == "B" or arm == "A_clean":
        tool_msg = _edit_tool_message(succ_text, post_lines, rel)
    elif arm == "A_nat":
        tool_msg = _edit_tool_message(a_nat_text, a_nat_lines, rel)
    else:
        raise ValueError(f"unknown arm {arm}")
    msgs.append(tool_msg)

    # user probe task (+ reminder) — identical across arms
    probe = trial["probe_instruction"]
    msgs.append({"role": "user", "content": probe + "\n\n" + REMINDER})
    return msgs


def _extract_first_edit_call(tool_calls):
    for tc in tool_calls or []:
        if tc.get("function", {}).get("name") == "edit_file":
            return tc
    return None


def run_arm(trial, arm, client, *, model, temperature):
    """Run one arm for one trial. Returns a result dict (single LLM call)."""
    t0 = time.time()
    try:
        msgs = build_messages(trial, arm)
    except Exception as exc:
        return {"arm": arm, "build_error": f"{type(exc).__name__}: {exc}", "wall_s": 0.0}

    resp, elapsed, err = llm_mod.call_once(client, msgs, prompt_assets.NATIVE_TOOLS,
                                          model=model, temperature=temperature)
    wall = time.time() - t0

    row = {
        "trial_id": trial["trial_id"],
        "mode": trial["mode"],
        "narrative": trial["narrative"],
        "relpath": trial["relpath"],
        "size_bucket": trial["size_bucket"],
        "language": trial["language"],
        "arm": arm,
        "temperature": temperature,
        "model": model,
        "wall_s": round(wall, 3),
        "llm_elapsed_s": round(elapsed, 3),
        "llm_error": err,
        "finish_reason": None,
        "tools_called": None,
        "first_tool_name": None,
        "edit_file_emitted": False,
        "emitted_edit_file_raw_args": None,
        "judge": None,
    }
    if err:
        return row
    if resp is None:
        row["llm_error"] = "no_response"
        return row

    row["finish_reason"] = resp.get("finish_reason")
    tool_calls = resp.get("tool_calls")
    row["tools_called"] = [tc["function"]["name"] for tc in tool_calls] if tool_calls else []
    row["first_tool_name"] = row["tools_called"][0] if row["tools_called"] else ("text_only" if resp.get("content") else "none")

    ec = _extract_first_edit_call(tool_calls)
    if ec is None:
        return row
    row["edit_file_emitted"] = True
    raw_args = ec["function"]["arguments"]
    row["emitted_edit_file_raw_args"] = raw_args

    # judge against the file state the model actually saw in THIS arm
    if arm == "B" or arm == "A_clean":
        judge_lines = trial["post_lines"]
    else:
        judge_lines = trial["a_nat_file_lines"]
    metrics = _engine.judge_probe_call(raw_args, judge_lines)
    row["judge"] = metrics
    # hoist headline metrics for easy aggregation
    for k in ("json_valid", "has_file_and_edits", "line_range_parses", "has_required_keys",
              "no_stray_keys", "template_structure_ok", "anchor_resolvable",
              "syntax_correct", "template_correct", "format_correct", "used_right_template",
              "n_edits", "n_stray_keys"):
        row[k] = metrics.get(k)
    row["violations"] = metrics.get("violations")
    row["engine_error"] = metrics.get("engine_error")
    # a convenience: did the emitted call use [TO] in content_to_remove?
    try:
        a = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        ed0 = a.get("edits", [{}])[0] if isinstance(a, dict) else {}
        row["emitted_has_to"] = "\n[TO]\n" in str(ed0.get("content_to_remove", ""))
        row["emitted_remove_line_number"] = ed0.get("remove_line_number")
    except Exception:
        row["emitted_has_to"] = None
        row["emitted_remove_line_number"] = None
    return row