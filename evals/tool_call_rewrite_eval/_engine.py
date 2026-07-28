"""Isolated import of AuroraCoder's RangeReplaceEditor + the exact edit helpers.

We load `AuroraCoder/src/code_tools/edit_file.py` directly via importlib so that
NONE of AuroraCoder's package-chain side-effect imports (google_search,
web_browser, subagent, tool_store_client, code_sandbox sandbox init) run. The
module itself only imports stdlib (os, json, tempfile, pathlib, ...), so loading
it in isolation is safe and dependency-free.

This gives us the *exact same* anchor-search / line-resolution / canonical
template logic that production uses, which we reuse for two purposes:

  1. Building the canonical "rewritten" Arm-B tool_call (the applied form the
     production `tool_executor` would write back into history) and the genuine
     success/error result-text strings that production emits.
  2. The deterministic *judge* for the probe edit_file call (does the model's
     emitted call use the right template and would it apply against the file?).
"""
import importlib.util
import json
import os
import pathlib
import shutil
import tempfile

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]  # .../AuroraCoder
_EDIT_FILE_PATH = _REPO_ROOT / "src" / "code_tools" / "edit_file.py"


def _load_editor_module():
    spec = importlib.util.spec_from_file_location("ac_edit_file_isolated", str(_EDIT_FILE_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_MOD = _load_editor_module()
RangeReplaceEditor = _MOD.RangeReplaceEditor


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def relpath_from(path, workspace_root):
    return os.path.relpath(str(path), str(workspace_root))


# ---------------------------------------------------------------------------
# Running an edit on a fresh throwaway copy (never mutates the original)
# ---------------------------------------------------------------------------
def run_edit_on_copy(src_file, edit_args_dict, workspace_root):
    """Copy `src_file` into a fresh temp workspace_root and run one edit_file call.

    `edit_args_dict` is a *full* edit_file arguments dict: {"file": relpath,
    "edits": [ ... ]}. Returns (result_text:str, applied|None, new_file_lines:list,
    new_file_text:str). The temp dir is removed afterwards; sources are untouched.
    """
    src_file = pathlib.Path(src_file)
    rel = relpath_from(src_file, workspace_root)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ac_eval_"))
    try:
        dst = tmp / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst)
        editor = RangeReplaceEditor(str(tmp))
        args = dict(edit_args_dict)
        args["file"] = rel
        result, applied = editor.edit(str(rel), args.get("edits", []))
        new_text = dst.read_text()
        new_lines = new_text.splitlines()
        return result, applied, new_lines, new_text
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def read_file_lines(src_file):
    return pathlib.Path(src_file).read_text().splitlines()


# ---------------------------------------------------------------------------
# Canonical applied form (what production tool_executor writes back to history)
# ---------------------------------------------------------------------------
def canonical_applied_edit(relpath, line_range, content_to_remove, replace_content):
    """Return the canonical edit-file *arguments* dict in the engine's applied form:
    {"file": relpath, "edits":[{"remove_line_number":"s-e",
    "content_to_remove":..., "replace_content":...}]}.

    `line_range` is (start_1indexed, end_1indexed) inclusive. Single-line ranges
    are rendered as "s-s" (matches the engine's applied-form builder, which always
    emits "s-e", never a bare "s").
    """
    s, e = line_range
    return {
        "file": relpath,
        "edits": [
            {
                "remove_line_number": f"{s}-{e}",
                "content_to_remove": content_to_remove,
                "replace_content": replace_content,
            }
        ],
    }


# ---------------------------------------------------------------------------
# Judge core: reuse the real engine's _validate_one_edit (anchor resolution) and
# _parse_line_range (line-range syntax) so the metric is deterministic &
# matches production acceptance exactly.
# ---------------------------------------------------------------------------
_VALIDATE_ONE = _MOD.RangeReplaceEditor._validate_one_edit
_PARSE_RANGE = _MOD.RangeReplaceEditor._parse_line_range


def parse_line_range(remove_line_number, total_lines):
    """Return (s_1indexed, e_1indexed) or an error string. Mirrors production."""
    try:
        return _PARSE_RANGE(remove_line_number, 0, total_lines)
    except Exception as exc:  # defensive: never let judge crash
        return f"judge_parse_exception:{exc!r}"


def validate_one_edit(edit_dict, file_lines, total_lines):
    """Return (applied_tuple | error_message_string) for a single edit dict."""
    # both helpers are staticmethods on RangeReplaceEditor
    return _VALIDATE_ONE(0, edit_dict, file_lines, total_lines)


def judge_probe_call(emitted_arguments, file_lines):
    """Judge a model's emitted edit_file *arguments* against the post-seed file.

    Returns a dict of metrics (all binary or counts) + diagnostics. Reads only.
    """
    metrics = {
        "json_valid": False,
        "has_file_and_edits": False,
        "line_range_parses": False,
        "has_required_keys": False,
        "no_stray_keys": False,
        "template_structure_ok": False,   # single-line -> no [TO]; multi-range -> exactly one [TO]
        "anchor_resolvable": False,        # engine _validate returns tuple (would apply)
        "n_edits": 0,
        "n_stray_keys": 0,
        "violations": [],
        "first_edit_remove_line_number": None,
        "first_edit_has_to": None,
        "first_edit_keys": None,
        "engine_error": None,
    }
    total = len(file_lines)

    # ---- json validity ----
    if isinstance(emitted_arguments, str):
        try:
            args = json.loads(emitted_arguments)
            metrics["json_valid"] = True
        except Exception as exc:
            metrics["violations"].append("json_parse_fail")
            metrics["engine_error"] = f"json_parse_fail:{exc!r}"
            _finalise(metrics)
            return metrics
    elif isinstance(emitted_arguments, dict):
        args = emitted_arguments
        metrics["json_valid"] = True
    else:
        metrics["violations"].append("args_not_dict")
        _finalise(metrics)
        return metrics

    # ---- file + edits present ----
    has_file = isinstance(args.get("file"), str) and args.get("file") != ""
    edits = args.get("edits")
    has_edits = isinstance(edits, list) and len(edits) > 0
    metrics["has_file_and_edits"] = bool(has_file and has_edits)
    if not metrics["has_file_and_edits"]:
        metrics["violations"].append("missing_file_or_edits")
        _finalise(metrics)
        return metrics

    metrics["n_edits"] = len(edits)
    ed = edits[0]
    keys = set(ed.keys()) if isinstance(ed, dict) else set()
    metrics["first_edit_keys"] = sorted(keys)

    required = {"remove_line_number", "content_to_remove", "replace_content"}
    present = required.issubset(keys) if isinstance(ed, dict) else False
    metrics["has_required_keys"] = bool(present)
    if not present:
        metrics["violations"].append("missing_required_keys")

    canonical_only = required
    stray = keys - canonical_only
    metrics["n_stray_keys"] = len(stray)
    metrics["no_stray_keys"] = (len(stray) == 0)
    if stray:
        metrics["violations"].append(f"stray_keys:{sorted(stray)}")

    rln = ed.get("remove_line_number") if isinstance(ed, dict) else None
    metrics["first_edit_remove_line_number"] = rln
    pr = parse_line_range(rln, total) if rln is not None else "missing"
    if isinstance(pr, str):
        metrics["violations"].append(f"line_range_parse_fail:{pr}")
    else:
        metrics["line_range_parses"] = True
        s, e = pr
        has_to = "\n[TO]\n" in str(ed.get("content_to_remove", ""))
        metrics["first_edit_has_to"] = has_to
        # template structure: single-line range must NOT use [TO]; multi-line must use [TO]
        if s == e:
            struct_ok = not has_to
        else:
            body = str(ed.get("content_to_remove", ""))
            cnt = body.count("\n[TO]\n")
            # exactly one [TO] and both halves non-empty
            if cnt == 1:
                left, right = body.split("\n[TO]\n", 1)
                struct_ok = bool(left.strip()) and bool(right.strip())
            else:
                struct_ok = False
        metrics["template_structure_ok"] = bool(struct_ok)
        if not struct_ok:
            metrics["violations"].append("template_structure_bad")

    # ---- anchor resolvable (real engine) ----
    if isinstance(ed, dict):
        try:
            res = validate_one_edit(ed, file_lines, total)
        except Exception as exc:
            res = f"validate_exception:{exc!r}"
        if isinstance(res, str):
            metrics["engine_error"] = res
            metrics["violations"].append("anchor_not_resolvable")
        else:
            metrics["anchor_resolvable"] = True
    else:
        metrics["violations"].append("edit_not_dict")

    _finalise(metrics)
    return metrics


def _finalise(metrics):
    """Derive the composite endpoints once partial metrics are set."""
    metrics["syntax_correct"] = bool(
        metrics["line_range_parses"] and metrics["has_required_keys"] and metrics["template_structure_ok"]
    )
    metrics["template_correct"] = bool(
        metrics["has_file_and_edits"]
        and metrics["line_range_parses"]
        and metrics["has_required_keys"]
        and metrics["no_stray_keys"]
        and metrics["template_structure_ok"]
    )
    # PRIMARY endpoint = model used the right template AND it would apply:
    metrics["format_correct"] = bool(metrics["template_correct"] and metrics["anchor_resolvable"])
    # headline user metric = "would use the right format":
    metrics["used_right_template"] = bool(metrics["template_correct"])