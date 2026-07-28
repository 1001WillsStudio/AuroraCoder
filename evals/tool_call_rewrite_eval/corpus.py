"""Corpus & trial generation for the tool-call-rewrite eval.

Copies a curated set of REAL repo files into `fixtures/pristine/samples/` (never
mutating originals — this is an eval-owned copy, satisfying the "do not edit the
dev branch" rule). Then generates trials per (file, mode, replicate).

A trial fully specifies the seeded prior-edit history and the probe task:
  - seed canonical edit (Arm B) and seed malformed edit per mode (Arm A)
  - genuine engine result texts (success from canonical; error from malformed)
  - post-seed file lines (file state the model sees in the code-interpreter panel,
    which the probe references; identical line count across arms/modes)
  - an independent probe region + natural-language probe instruction
"""
import json
import os
import pathlib
import random
import shutil

from . import _engine
from . import edits

PKG_DIR = pathlib.Path(__file__).resolve().parent
FIXTURES = PKG_DIR / "fixtures"
PRISTINE = FIXTURES / "pristine" / "samples"
WORKSPACE = FIXTURES / "workspace"

# Expanded corpus: more independent source files so multi-line block modes
# (F3) have enough distinct trials to reach significance. These are COPIED into
# the eval's own fixtures dir; originals on the dev branch are never edited.
CANDIDATE_REAL_FILES = [
    "AuroraCoder/src/training_log.py",
    "AuroraCoder/src/code_tools/jupyter_code_runner.py",
    "AuroraCoder/src/core_tools/continue_chat.py",
    "AuroraCoder/src/core_tools/subagent.py",
    "AuroraCoder/src/core_tools/google_search.py",
    "AuroraCoder/src/core_tools/tool_store_client.py",
    "AuroraCoder/src/web_api/__init__.py",
    "examples/echo_mcp_server.py",
    "AgentToolStore/server/init_db.py",
    "AgentToolStore/server/auth.py",
    "AgentToolStore/server/app/main.py",
    "test_toolsets/calculator/toolset.py",
    "test_toolsets/text-transform/toolset.py",
    "test_toolsets/text-gen/toolset.py",
    "test_toolsets/file-verify/toolset.py",
    "AuroraCoder/optional-mcp/schedule-daemon/server.py",
    "AuroraCoder/src/providers.py",
    "AuroraCoder/src/tool_executor.py",
]


def prepare_pristine():
    """Copy candidate real files into fixtures/pristine/samples/. Returns relpaths."""
    PRISTINE.parent.mkdir(parents=True, exist_ok=True)
    if PRISTINE.exists():
        shutil.rmtree(PRISTINE)
    PRISTINE.mkdir(parents=True)
    ws_root = pathlib.Path("/workspace")
    relpaths = []
    for f in CANDIDATE_REAL_FILES:
        src = ws_root / f
        if not src.exists():
            continue
        dst = PRISTINE / src.name
        # avoid collisions
        n = 2
        while dst.exists():
            dst = PRISTINE / f"{src.stem}_{n}{src.suffix}"
            n += 1
        shutil.copy2(src, dst)
        relpaths.append(os.path.join("samples", dst.name))
    return relpaths


# ---------------------------------------------------------------------------
# Region finding
# ---------------------------------------------------------------------------
def _has_identifier(line):
    return edits.first_identifier(line) is not None


def good_single_lines(lines):
    """Return list of (L_1idx, normalised_line) of unique, identifier-bearing lines."""
    from collections import Counter
    stripped = [edits.normalize_line(l) for l in lines]
    cnt = Counter(stripped)
    out = []
    for i, s in enumerate(stripped):
        if len(s) < 6 or len(s) > 140:
            continue
        if not s.strip():
            continue
        if cnt[s] != 1:
            continue
        if not _has_identifier(s):
            continue
        out.append((i + 1, s))
    return out


def good_multi_blocks(lines, max_k=3):
    """Return list of (L, L+k, first_norm, last_norm) where first/last lines are
    unique, identifier-bearing, range length 2..max_k+1 lines."""
    sing = good_single_lines(lines)
    sing_set = {L: s for L, s in sing}
    out = []
    for k in range(1, max_k + 1):
        for L, first in sing:
            end = L + k
            if end not in sing_set:
                continue
            last = sing_set[end]
            out.append((L, end, first, last))
    return out


def _pick(rng, items, used):
    """Pick an item from `items` (list) whose start/end is far from any used span.
    Returns item or None."""
    tries = 0
    while tries < 200:
        tries += 1
        if not items:
            return None
        it = rng.choice(items)
        start = it[0]
        end = it[1] if len(it) > 2 and isinstance(it[1], int) else it[0]
        # it shape for single: (L, s) ; for multi: (L, end, first, last)
        if len(it) >= 2 and isinstance(it[1], str):
            end = it[0]
        ok = all(abs(start - us) > 8 and abs(end - us) > 8 for us in used)
        if ok:
            return it
    return None


# ---------------------------------------------------------------------------
# Probe instruction construction
# ---------------------------------------------------------------------------
def build_probe_single(post_lines, P, relpath):
    line = edits.normalize_line(post_lines[P - 1])
    tok = edits.first_identifier(line)
    new = tok + "zz" if tok else None
    if not tok:
        return None
    repl_line = line.replace(tok, new, 1)
    instr = (
        f"Now make one more small edit to the SAME file (`{relpath}`). "
        f"On the line that reads: `{line}` (this is line {P} in the code "
        f"interpreter display), rename the identifier `{tok}` to `{new}`. "
        f"Use a single edit_file call."
    )
    return instr


def build_probe_multi(post_lines, P, Q, relpath):
    # NOTE: deliberately NEUTRAL about how to format content_to_remove. We state
    # only the line range and the new block content — the model must decide for
    # itself whether to use the canonical `first\n[TO]\nlast` form or paste the
    # full block. This is what lets the prior-call template (Arm A vs B) bias the
    # model's format CHOICE in a measurable way.
    first = edits.normalize_line(post_lines[P - 1])  # noqa: F841 (kept for sanity logs)
    last = edits.normalize_line(post_lines[Q - 1])
    tok = edits.first_identifier(last)
    if not tok:
        return None
    new = tok + "zz"
    new_lines = [edits.normalize_line(l) for l in post_lines[P - 1:Q]]
    new_lines[-1] = new_lines[-1].replace(tok, new, 1)
    new_block = "\n".join(new_lines)
    instr = (
        f"Now make one more small edit to the SAME file (`{relpath}`). "
        f"Replace the lines from line {P} through line {Q} with the following "
        f"content:\n\n```\n{new_block}\n```\n\nUse a single edit_file call."
    )
    return instr


# ---------------------------------------------------------------------------
# Trial generation
# ---------------------------------------------------------------------------
def generate_trials(relpaths, replicates_per_mode, seed=0):
    trials = []
    rng_master = random.Random(seed)
    for relpath in relpaths:
        pristine_file = PRISTINE.parent / relpath
        text = pathlib.Path(pristine_file).read_text()
        lines = text.splitlines()
        size = len(lines)
        size_bucket = "small" if size <= 50 else ("medium" if size <= 300 else "large")
        lang = "python" if pristine_file.suffix == ".py" else "other"
        sing = good_single_lines(lines)
        mult = good_multi_blocks(lines)
        if not sing:
            continue
        for mode in list(edits.MODES):
            for rep in range(replicates_per_mode):
                rng = random.Random(rng_master.randint(0, 2 ** 31 - 1))
                t = _make_trial(relpath, mode, rep, lines, size, size_bucket, lang,
                                sing, mult, rng)
                if t is None:
                    continue
                trials.append(t)
    return trials


def _make_trial(relpath, mode, rep, lines, size, size_bucket, lang, sing, mult, rng):
    # ---- seed region ----
    if mode == "F3":
        if not mult:
            return None
        it = _pick(rng, mult, used=set())
        if it is None:
            return None
        L, Lk, first, last = it
        seed_region = {"start": L, "end": Lk, "kind": "multi"}
        # block repl: rename an identifier in the last line, keep line count
        last_idx = Lk - 1
        first_idx = L - 1
        block_lines = [edits.normalize_line(l) for l in lines[first_idx: Lk]]
        tok = edits.first_identifier(block_lines[-1])
        if not tok:
            return None
        new = tok + "zz"
        block_lines[-1] = block_lines[-1].replace(tok, new, 1)
        repl_block = "\n".join(block_lines)
        canonical = edits.canonical_multi(relpath, L, Lk, first, last, repl_block)
        # F3 malformed: the FULL block joined by newlines (NO [TO]) as
        # content_to_remove; same intended replace_content. This is the
        # non-canonical call the engine still auto-corrects via whole-file
        # multi-line block search.
        full_block = "\n".join([edits.normalize_line(l) for l in lines[first_idx: Lk]])
        malformed = {"file": relpath, "edits": [{
            "remove_line_number": f"{L}-{Lk}",
            "content_to_remove": full_block,
            "replace_content": repl_block,
        }]}
    else:
        it = _pick(rng, sing, used=set())
        if it is None:
            return None
        L, s = it
        seed_region = {"start": L, "end": L, "kind": "single"}
        tok = edits.first_identifier(s)
        if not tok:
            return None
        new = tok + "zz"
        repl_line = s.replace(tok, new, 1)
        canonical = edits.canonical_single(relpath, L, s, repl_line)

    if mode != "F3":
        malformed = edits.build_malformed(mode, canonical, seed_region)
        if malformed is None:
            return None

    # ---- run canonical seed on a fresh copy -> success_text + post_lines ----
    src_file = PRISTINE.parent / relpath
    succ_text, applied, post_lines, post_text = _engine.run_edit_on_copy(
        src_file, canonical, str(PRISTINE.parent))
    if applied is None:
        # canonical should always apply; skip if not
        return None

    # ---- run malformed seed on a fresh copy -> natural text + applies? ----
    mal_text, mal_applied, _, _ = _engine.run_edit_on_copy(
        src_file, malformed, str(PRISTINE.parent))
    natural_safe = "⚠️ Original parameters were auto-corrected." in mal_text
    info = edits.MODES[mode]
    if info["narrative"] == "error":
        if mal_applied is not None:
            return None  # error mode must actually error
        error_text = mal_text
    else:
        if mal_applied is None:
            return None  # apply mode must apply
        error_text = None

    # Arm A_nat: natural production behaviour (the real result text + file state
    # the model would actually see). For apply modes the malformed edit's final
    # file equals the canonical final file; for error modes the file is pristine.
    if mal_applied is not None:
        a_nat_file_lines = post_lines
    else:
        a_nat_file_lines = lines

    # ---- probe region ----
    used = {seed_region["start"], seed_region["end"]}
    probe_kind = "multi"
    probe_region = None
    probe_instr = None
    if mode == "F3":
        pit = _pick(rng, mult, used=used)
        if pit is None:
            return None
        P, Q, _, _ = pit
        probe_instr = build_probe_multi(post_lines, P, Q, relpath)
        probe_region = {"start": P, "end": Q}
    else:
        pit = _pick(rng, sing, used=used)
        if pit is None:
            return None
        P, s = pit
        probe_instr = build_probe_single(post_lines, P, relpath)
        probe_region = {"start": P, "end": P}
    if probe_instr is None:
        return None

    # sanity: post_lines count == lines count (seed didn't change line count)
    if len(post_lines) != size:
        return None

    trial_id = f"{pathlib.Path(relpath).stem}__{mode}__{rep}"
    return {
        "trial_id": trial_id,
        "relpath": relpath,
        "mode": mode,
        "narrative": info["narrative"],
        "size": size,
        "size_bucket": size_bucket,
        "language": lang,
        "seed_region": seed_region,
        "seed_canonical": canonical,
        "seed_malformed": malformed,
        "seed_success_text": succ_text,
        "seed_error_text": error_text,
        "seed_malformed_natural_text": mal_text,
        "seed_malformed_applied": (mal_applied is not None),
        "seed_malformed_auto_corrected_header": natural_safe,
        "pristine_lines": lines,
        "post_lines": post_lines,
        "post_text": post_text,
        # Arm A_nat (natural production behaviour):
        "a_nat_file_lines": a_nat_file_lines,
        "a_nat_result_text": mal_text,
        "probe_kind": probe_kind,
        "probe_region": probe_region,
        "probe_instruction": probe_instr,
    }


def load_or_build_trials(replicates, seed=0, force=False):
    cache = FIXTURES / "trials.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text())
    relpaths = prepare_pristine()
    trials = generate_trials(relpaths, replicates, seed=seed)
    cache.write_text(json.dumps(trials, ensure_ascii=False, indent=2))
    return trials


if __name__ == "__main__":
    relpaths = prepare_pristine()
    print("pristine relpaths:", relpaths)
    trials = generate_trials(relpaths, 3, seed=0)
    print(f"generated {len(trials)} trials")
    from collections import Counter
    print(Counter(t["mode"] for t in trials))
    print(json.dumps(trials[0], indent=2)[:1500])