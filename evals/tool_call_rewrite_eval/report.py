"""Aggregate results.jsonl into a markdown report with paired statistics.

Because each trial is run under every arm, we use PAIRED statistics:
  * matched discordance (B-right/A-wrong vs B-wrong/A-right) and a McNemar test,
  * a bootstrap confidence interval over per-trial signed differences.
This correctly accounts for trial-level difficulty (some probes are easy for
both arms; some hard) instead of pooling as if arms were independent samples.

Reads AuroraCoder/evals/tool_call_rewrite_eval/results/results.jsonl
Writes research/tool_call_rewrite_results.md
"""
import argparse
import collections
import json
import math
import pathlib
import random

METRIC_PRIMARY = "used_right_template"   # model used the right canonical template
METRIC_TO = "emitted_has_to"             # [TO] usage (only meaningful for multi-line probes)
METRIC_FMT = "format_correct"            # template + would-apply (anchor resolvable)
METRIC_ANCHOR = "anchor_resolvable"
ARMS = ["B", "A_clean", "A_nat"]


def load_rows(path):
    rows = []
    for line in pathlib.Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def rate(rows, metric, emitted_only=True):
    sub = [r for r in rows if (r.get("edit_file_emitted") or not emitted_only)]
    if not sub:
        return 0.0, 0, 0
    ok = sum(1 for r in sub if r.get(metric))
    return ok / len(sub), ok, len(sub)


def by_group(rows, keys):
    g = collections.defaultdict(list)
    for r in rows:
        k = tuple(r.get(k2) for k2 in keys)
        g[k].append(r)
    return g


def trials_per_arm(rows):
    """Return {(trial_id): {arm: row}} for emitted rows."""
    out = collections.defaultdict(dict)
    for r in rows:
        if r.get("edit_file_emitted"):
            out[r["trial_id"]][r["arm"]] = r
    return out


def paired(rows, arm_b_key, arm_a_key, metric, key_filter=None):
    per = trials_per_arm(rows)
    b = c = bb = cc = 0  # B&T : c=B-right&A-wrong; b=B-wrong&A-right
    diffs = []
    for tid, arms in per.items():
        if arm_b_key not in arms or arm_a_key not in arms:
            continue
        if key_filter is not None and not key_filter(arms[arm_b_key]):
            continue
        yb = 1 if arms[arm_b_key].get(metric) else 0
        ya = 1 if arms[arm_a_key].get(metric) else 0
        diffs.append(yb - ya)
        if yb and not ya:
            c += 1
        elif ya and not yb:
            b += 1
    n = len(diffs)
    mean_diff = sum(diffs) / n if n else 0.0
    # McNemar (with continuity correction)
    mcn = (abs(b - c) - 1) ** 2 / (b + c) if (b + c) > 0 else 0.0
    p = chi2_sf(mcn, 1) if (b + c) > 0 else 1.0
    ci = bootstrap_ci(diffs)
    return {
        "n_pairs": n,
        "B_right_A_wrong": c, "B_wrong_A_right": b,
        "mcnemar_chi2": mcn, "p_value": p,
        "mean_diff": mean_diff,
        "ci_low": ci[0], "ci_high": ci[1],
    }


def chi2_sf(x, df):
    """Upper-tail survival function for chi-square with df=1."""
    # chi2_1 = Z^2; sf = 2*(1 - Phi(sqrt(x)))
    z = math.sqrt(max(x, 0.0))
    return 2 * (1 - _normal_cdf(z))


def _normal_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2.0)))


def bootstrap_ci(diffs, reps=4000, seed=12345):
    if not diffs:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(diffs)
    means = []
    for _ in range(reps):
        s = 0.0
        for _ in range(n):
            s += diffs[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int(0.025 * reps)]
    hi = means[int(0.975 * reps)]
    return (lo, hi)


def fmt_pct(x):
    return f"{x*100:.1f}%"


def build_report(rows, temps):
    lines = []
    lines.append("# Tool-Call Rewrite Evaluation — Results\n")
    lines.append("Flow:<br/>Arm-B uses canonical (rewritten) prior edit_file call + clean success text;<br/>"
                 "Arm A_clean uses the MALFORMED prior call but with the SAME clean success narrative+file (isolates the **call text**);<br/>"
                 "Arm A_nat uses the malformed prior call + its NATURAL production result (error/auto-corrected) + actual file outcome.")
    lines.append("")
    lines.append(f"Data points: {len(rows)} rows (model manifest in file names).")
    lines.append("")

    primary_metric = METRIC_PRIMARY
    for temp in temps:
        trows = [r for r in rows if abs(r.get("temperature", -9) - temp) < 1e-6]
        lines.append(f"\n## Temperature = {temp}  (n_rows={len(trows)})\n")
        lines.append("### Overall rates per arm (only rows where the model emitted an edit_file call)")
        lines.append("")
        lines.append("| Arm | n_emit | used_right_template | format_correct | anchor_resolvable | emit_rate |")
        lines.append("|---|---|---|---|---|---|")
        for arm in ARMS:
            ar = [r for r in trows if r["arm"] == arm]
            rmt, okr, nr = rate(ar, METRIC_PRIMARY)
            rfc, _, _ = rate(ar, METRIC_FMT)
            ra, _, _ = rate(ar, METRIC_ANCHOR)
            emit_rate = (sum(1 for r in ar if r.get("edit_file_emitted")) / len(ar)) if ar else 0
            lines.append(f"| {arm} | {nr} | {fmt_pct(rmt)} | {fmt_pct(rfc)} | {fmt_pct(ra)} | {fmt_pct(emit_rate)} |")

        # ---- by mode ----
        lines.append("\n### Per-mode `used_right_template` rate (cached metric)")
        lines.append("")
        lines.append("| Mode | narrative | n_emit | B | A_clean | A_nat |")
        lines.append("|---|---|---|---|---|---|")
        mrows = by_group(trows, ["mode"])
        for (mode,), _ in sorted(mrows.items()):
            modrows = mrows[(mode,)]
            narr = modrows[0].get("narrative")
            for arm in ARMS:
                ok = modrows[0]  # placeholder
            rates = {}
            n_emit = {}
            for arm in ARMS:
                r, ok, nr = rate([r for r in modrows if r["arm"] == arm], METRIC_PRIMARY)
                rates[arm] = r
                n_emit[arm] = nr
            lines.append(f"| {mode} | {narr} | {n_emit['B']} | {fmt_pct(rates['B'])} | {fmt_pct(rates['A_clean'])} | {fmt_pct(rates['A_nat'])} |")

        # ---- [TO] usage on F3 (multi-line probes) ----
        f3rows = [r for r in trows if r["mode"] == "F3"]
        if f3rows:
            lines.append("\n### [TO] usage (F3 multi-line probes only) — does the prior call teach [TO]?")
            lines.append("")
            lines.append("| Arm | n_emit | uses_[TO] rate |")
            lines.append("|---|---|---|")
            for arm in ARMS:
                ar = [r for r in f3rows if r["arm"] == arm and r.get("edit_file_emitted")]
                ok = sum(1 for r in ar if r.get(METRIC_TO))
                lines.append(f"| {arm} | {len(ar)} | {fmt_pct(ok/len(ar)) if ar else 0} |")

        # ---- paired statistics (PRIMARY: B vs A_clean) ----
        lines.append("\n### Paired comparison — PRIMARY (B vs A_clean), metric=used_right_template")
        lines.append("")
        p = paired(trows, "B", "A_clean", METRIC_PRIMARY)
        lines.append(f"- n paired trials = {p['n_pairs']}")
        lines.append(f"- B-right & A-wrong (rewrite wins): **{p['B_right_A_wrong']}**")
        lines.append(f"- B-wrong & A-right (rewrite loses): **{p['B_wrong_A_right']}**")
        lines.append(f"- mean per-trial diff (B - A_clean) = **{p['mean_diff']:+.3f}** &nbsp; (positive = rewrite helps)")
        lines.append(f"- bootstrap 95% CI for the diff: **[{p['ci_low']:+.3f}, {p['ci_high']:+.3f}]**")
        lines.append(f"- McNemar χ² = {p['mcnemar_chi2']:.2f}, p = {p['p_value']:.4f}")
        verdict = "SUPPORTS" if (p["mean_diff"] > 0 and p["ci_low"] > 0) else (
            "SUGGESTIVE" if p["mean_diff"] > 0 else ("OPPOSITE" if p["mean_diff"] < 0 else "NULL"))
        lines.append(f"- Verdict: **{verdict}** the tool-call-rewrite teaching hypothesis.")

        lines.append("\n### Paired comparison — SECONDARY (B vs A_nat), metric=used_right_template")
        lines.append("")
        p2 = paired(trows, "B", "A_nat", METRIC_PRIMARY)
        lines.append(f"- n paired trials = {p2['n_pairs']}")
        lines.append(f"- B-right & A_nat-wrong: {p2['B_right_A_wrong']}  |  B-wrong & A_nat-right: {p2['B_wrong_A_right']}")
        lines.append(f"- mean per-trial diff (B - A_nat) = {p2['mean_diff']:+.3f}  95% CI [{p2['ci_low']:+.3f}, {p2['ci_high']:+.3f}]")
        lines.append(f"- McNemar χ² = {p2['mcnemar_chi2']:.2f}, p = {p2['p_value']:.4f}")

        # ---- banner/narrative decomposition (A_clean vs A_nat) ----
        # Both arms carry the SAME malformed prior call; they differ only in the
        # tool *result* narrative. Isolates the effect of the
        # "⚠️ Original parameters were auto-corrected" banner that production
        # emits on auto-corrected edits.
        lines.append("\n### Banner/narrative decomposition (A_clean vs A_nat)")
        lines.append("")
        lines.append("Holding the malformed prior CALL fixed, this compares ONLY the tool result narrative: "
                     "A_clean uses the clean success text; A_nat uses the genuine production text "
                     "(apply modes → the \"⚠️ auto-corrected\" banner + success; error modes → the "
                     "error string + unchanged file). A positive (A_clean - A_nat) diff means the clean "
                     "narrative yields MORE canonical follow-up calls than the auto-correct banner.")
        lines.append("")
        lines.append("| Group | n_pairs | mean diff (A_clean - A_nat) | 95% CI | McNemar p | readout |")
        lines.append("|---|---|---|---|---|---|")
        for label, grp in [("ALL", trows),
                           ("F3 (apply / banner present)", [r for r in trows if r["mode"] == "F3"]),
                           ("error modes (F1/F2/F4)", [r for r in trows if r["mode"] in ("F1", "F2", "F4")])]:
            if not grp:
                continue
            pn = paired(grp, "A_clean", "A_nat", METRIC_PRIMARY)
            read = ("clean-banner HELPS" if pn["mean_diff"] > 0 and pn["ci_low"] > 0 else
                    "clean-banner HURTS" if pn["mean_diff"] < 0 and pn["ci_high"] < 0 else
                    "no clear effect")
            lines.append(f"| {label} | {pn['n_pairs']} | {pn['mean_diff']:+.3f} | "
                         f"[{pn['ci_low']:+.3f}, {pn['ci_high']:+.3f}] | {pn['p_value']:.4f} | {read} |")
        lines.append("")
        lines.append("Interpretation: if A_clean > A_nat on F3 (where the auto-correct banner is what A_nat "
                     "actually shows), it suggests the \"⚠️ auto-corrected, no action needed\" banner may "
                     "*undermine* the teaching signal — it tells the model its bad params 'didn't matter', "
                     "reducing the pressure to adopt the canonical form itself.")

        # per-mode PRIMARY paired diffs
        lines.append("\n### Per-mode PRIMARY (B vs A_clean) paired diffs (positive = rewrite helps)")
        lines.append("")
        lines.append("| Mode | narrative | n_pairs | mean diff | 95% CI | McNemar p |")
        lines.append("|---|---|---|---|---|---|")
        for (mode,), _ in sorted(mrows.items()):
            modrows = mrows[(mode,)]
            pm = paired(modrows, "B", "A_clean", METRIC_PRIMARY)
            lines.append(f"| {mode} | {modrows[0].get('narrative')} | {pm['n_pairs']} | {pm['mean_diff']:+.3f} | "
                         f"[{pm['ci_low']:+.3f}, {pm['ci_high']:+.3f}] | {pm['p_value']:.4f} |")

    lines.append("\n## Verdict summary\n")
    p_overall = paired(rows, "B", "A_clean", METRIC_PRIMARY)
    if p_overall["mean_diff"] > 0 and p_overall["ci_low"] > 0:
        verdict = "**SUPPORTS** — the silent tool-call rewrite measurably increases the rate at which the agent's NEXT edit_file call uses the canonical template."
    elif p_overall["mean_diff"] > 0 and p_overall["ci_low"] <= 0:
        verdict = "**SUGGESTIVE but not significant** — a positive direction, but the 95% CI crosses zero at this sample size."
    elif abs(p_overall["mean_diff"]) < 1e-9:
        verdict = "**NULL** — no detectable effect of the rewrite on the format of the next call at this sample size."
    else:
        verdict = "**OPPOSITE** — leaving the wrong call in history produced at least as many canonical follow-up calls as rewriting it."
    v2 = ([r for r in rows if r["arm"]=="B" and r.get("edit_file_emitted")])
    a2 = ([r for r in rows if r["arm"]=="A_clean" and r.get("edit_file_emitted")])
    rB = sum(1 for r in v2 if r.get(METRIC_PRIMARY))/len(v2) if v2 else 0
    rA = sum(1 for r in a2 if r.get(METRIC_PRIMARY))/len(a2) if a2 else 0
    lines.append(f"\nOverall `used_right_template` rate: B = {fmt_pct(rB)}, A_clean = {fmt_pct(rA)}, "
                 f"paired mean diff (B-A_clean) = {p_overall['mean_diff']:+.3f}, "
                 f"95% CI [{p_overall['ci_low']:+.3f}, {p_overall['ci_high']:+.3f}].")
    lines.append("")
    lines.append("### " + verdict)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in_", dest="inp", default="AuroraCoder/evals/tool_call_rewrite_eval/results/results.jsonl")
    ap.add_argument("--out", default="research/tool_call_rewrite_results.md")
    args = ap.parse_args()
    rows = load_rows(args.inp)
    temps = sorted(set(r.get("temperature") for r in rows))
    md = build_report(rows, temps)
    pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(args.out).write_text(md)
    print(md)
    print(f"\n[report written to {args.out}]")


if __name__ == "__main__":
    main()