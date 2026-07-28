"""Canonical + malformed edit_file argument builders (the experimental manipulation).

For every trial we derive, from a real file region, the *canonical* (Arm-B) edit
arguments (the form `tool_executor` writes back into history — always "s-e", with
single-line `content_to_remove` for single-line ranges and `first\\n[TO]\\nlast`
for multi-line ranges) and a *malformed* (Arm-A) variant per failure mode.

Two narrative classes:
  * `apply` modes (F1/F3/F5/F6): the malformed call is auto-corrected by the real
    engine and WOULD apply (in production it gets the silent rewrite). For the
    *primary* comparison we hold the tool-result text identical across arms
    (the canonical edit's clean "✅ Applied…" text) so the ONLY difference between
    Arm A and Arm B is the assistant's prior tool_call text — a razor-clean test
    of "does seeing a canonical vs non-canonical prior call teach the format".
  * `error` modes (F2/F4): the malformed call genuinely errors (anchor not found /
    reversed range). We use the genuine error text for Arm A and the genuine
    success text for Arm B — the *natural production-behaviour* comparison (it
    also confounds "wrong call" with "error narrative"), reported as secondary.
"""
import re

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def first_identifier(line):
    m = IDENT_RE.search(line)
    return m.group(0) if m else None


def normalize_line(line):
    """Strip trailing newline/CR; do NOT strip leading indent (indent is semantic)."""
    return line.rstrip("\n").rstrip("\r")


def canonical_single(relpath, start_1idx, line_text, replace_text):
    s = start_1idx
    return {
        "file": relpath,
        "edits": [
            {
                "remove_line_number": f"{s}-{s}",
                "content_to_remove": line_text,
                "replace_content": replace_text,
            }
        ],
    }


def canonical_multi(relpath, start_1idx, end_1idx, first_line, last_line, replace_text):
    return {
        "file": relpath,
        "edits": [
            {
                "remove_line_number": f"{start_1idx}-{end_1idx}",
                "content_to_remove": first_line + "\n[TO]\n" + last_line,
                "replace_content": replace_text,
            }
        ],
    }


def _typo(line_text):
    """Change the last alphanumeric char of an identifier in the line (anchor typo)."""
    m = list(IDENT_RE.finditer(line_text))
    if not m:
        return None
    tok = m[-1]
    s, e = tok.start(), tok.end()
    if e - s < 1:
        return None
    # bump last char
    lc = line_text[s:e][-1]
    nw = chr(ord(lc) + 1) if lc not in "zZ9" else lc + "x"
    new_tok = line_text[s:e][:-1] + nw
    return line_text[:s] + new_tok + line_text[e:]


def build_malformed(mode, canonical, seed_region):
    """Return a malformed edit_args dict for the given mode, derived from canonical."""
    edits = canonical["edits"]
    e0 = edits[0]
    rln = e0["remove_line_number"]
    ctr = e0["content_to_remove"]
    repl = e0["replace_content"]
    s = seed_region["start"]
    e = seed_region["end"]

    if mode == "F1":  # wrong line numbers (+10), correct anchor
        shift = 10
        ns, ne = s + shift, e + shift
        return {"file": canonical["file"], "edits": [{
            "remove_line_number": f"{ns}-{ne}",
            "content_to_remove": ctr,
            "replace_content": repl,
        }]}

    if mode == "F2":  # typo in anchor -> genuinely errors
        if seed_region["kind"] != "single":
            # F2 defined for single-line seeds
            return None
        typo = _typo(ctr)
        if typo is None:
            return None
        return {"file": canonical["file"], "edits": [{
            "remove_line_number": rln,
            "content_to_remove": typo,
            "replace_content": repl,
        }]}

    if mode == "F3":  # no [TO] on a multi-line range; content = full block
        if seed_region["kind"] != "multi":
            return None
        # full block = lines joined; canonical ctr was "first\n[TO]\nlast"
        first, last = ctr.split("\n[TO]\n", 1)
        # we don't have interior lines here; approximate full block, which fails
        # the canonical structure test and (in story) "applies" via no-[TO] path.
        full_block = ctr.replace("\n[TO]\n", "\n")
        return {"file": canonical["file"], "edits": [{
            "remove_line_number": rln,
            "content_to_remove": full_block,
            "replace_content": repl,
        }]}

    if mode == "F4":  # reversed range + a stray key (schema mixing)
        return {"file": canonical["file"], "edits": [{
            "remove_line_number": f"{e}-{s}" if s != e else f"{s + 1}-{s}",
            "content_to_remove": ctr,
            "replace_content": repl,
            "end_line_number": f"{s}",
        }]}

    if mode == "F5":  # single-line content announced as a 2-line range
        if seed_region["kind"] != "single":
            return None
        return {"file": canonical["file"], "edits": [{
            "remove_line_number": f"{s}-{s + 1}",
            "content_to_remove": ctr,
            "replace_content": repl,
        }]}

    if mode == "F6":  # correct anchor + wrong indent in replace_content
        return {"file": canonical["file"], "edits": [{
            "remove_line_number": rln,
            "content_to_remove": ctr,
            "replace_content": repl.lstrip(),
        }]}

    raise ValueError(f"unknown mode {mode}")


# Mode registry
# Note: empirically, a SINGLE-LINE edit with a wrong line-number hint (>3 lines off) does
# NOT fall back to whole-file search, so it genuinely ERRORS. Whole-file fallback only
# exists for MULTI-line block anchors. So F1 (wrong single-line line numbers) is an
# ERROR mode, not an apply mode.

MODES = {
    "F1": {"narrative": "error", "desc": "wrong single-line line numbers (+10), correct anchor — genuinely errors (no whole-file fallback for single-line anchors)"},
    "F2": {"narrative": "error", "desc": "typo in anchor header — genuinely errors (anchor not found)"},
    "F3": {"narrative": "apply", "desc": "multi-line range, missing [TO] (content=full block) — engine auto-corrects via whole-file block search; teaches the [TO] template"},
    "F4": {"narrative": "error", "desc": "reversed range + stray `end_line_number` key — genuinely errors (parse sl>el)"},
    "F5": {"narrative": "apply", "desc": "single-line content announced as a 2-line range — engine truncates end to the single anchor; teaches exact-range template"},
    "F6": {"narrative": "apply", "desc": "correct anchor, wrong indentation in replace_content — engine auto-fixes indent"},
}

# Apply modes (malformed call IS auto-corrected by the engine -> gets the silent
# rewrite in production). PRIMARY comparison isolates call-text by holding the
# tool-result text + file state identical across arms.
APPLY_MODES = ["F3", "F5", "F6"]
# Error modes (malformed call genuinely errors -> production leaves it + error text).
ERROR_MODES = ["F1", "F2", "F4"]
# Back-compat aliases:
PRIMARY_MODES = APPLY_MODES
SECONDARY_MODES = ERROR_MODES