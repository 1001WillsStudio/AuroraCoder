"""Aggregate multiturn.jsonl into a per-turn trace report + per-turn compounding
statistics.

Each (trial_id, arm, temperature) is a TRAJECTORY of turns T1=seed..T{n+1}=agent.
Older incomplete runs are superseded by the latest complete run_token.

Reads  AuroraCoder/evals/tool_call_rewrite_eval/results/multiturn.jsonl
Writes  research/tool_call_rewrite_trajectories.md
"""
import argparse
import collections
import json
import math
import pathlib

PKG = pathlib.Path(__file__).resolve().parent.parent.parent.parent  # workspace


def load_rows(path):
    return [json.loads(l) for l in pathlib.Path(path).read_text().splitlines() if l.strip()]


def latest_trajectory_per_key(rows, n_expected):
    """Keep, per (trial_id, arm, temperature), the rows from the LATEST run_token
    that has the FULL set of turns 1..n_expected (in order) — i.e. the most
    recent complete trajectory. Returns dict[(tid,arm,temp)] -> [turn rows sorted]."""
    by_key = collections.defaultdict(list)
    for r in rows:
        key = (r["trial_id"], r["arm"], r.get("temperature"))
        by_key[key].append(r)
    out = {}
    for key, rs in by_key.items():
        # group by run_token, keep the one with max token that is complete
        tokens = collections.defaultdict(list)
        for r in rs:
            tokens[str(r.get("run_token", ""))].append(r)
        best = None
        for tok, trs in tokens.items():
            if len(trs) != n_expected:
                continue
            turns = sorted(trs, key=lambda x: x["turn"])
            if [t["turn"] for t in turns] == list(range(1, n_expected + 1)):
                # prefer the latest run_token (string sort of timestamp-ish)
                if best is None or tok > best[0]:
                    best = (tok, turns)
        if best is not None:
            out[key] = best[1]
    return out


def per_turn_rate(trajs, turn, metric):
    vals = []
    for rows in trajs.values():
        if len(rows) >= turn:
            v = rows[turn - 1].get(metric)
            if v is not None:
                vals.append(1 if v else 0)
    return (sum(vals) / len(vals)) if vals else 0.0, len(vals)


def paired_per_turn(trajs_A, trajs_B, turn, metric):
    """Paired (B vs A) at a given turn. Returns diff + bootstrap CI + n."""
    diffs = []
    # match on trial_id
    a_index = {(k[0], k[2]): v for k, v in trajs_A.items()}
    for (tid, arm, temp), brows in trajs_B.items():
        arows = a_index.get((tid, temp))
        if arows is None or len(brows) < turn or len(arows) < turn:
            continue
        bv = brows[turn - 1].get(metric)
        av = arows[turn - 1].get(metric)
        if bv is None or av is None:
            continue
        diffs.append((1 if bv else 0) - (1 if av else 0))
    n = len(diffs)
    if n == 0:
        return {"n": 0, "diff": 0.0, "ci": (0, 0)}
    m = sum(diffs) / n
    return {"n": n, "diff": m, "ci": bootstrap_ci(diffs)}


def bootstrap_ci(diffs, reps=4000, seed=12345):
    import random
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
    return (means[int(0.025 * reps)], means[int(0.975 * reps)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in_", dest="inp", default="AuroraCoder/evals/tool_call_rewrite_eval/results/multiturn.jsonl")
    ap.add_argument("--out", default="research/tool_call_rewrite_trajectories.md")
    ap.add_argument("--n-phases", type=int, default=3)
    args = ap.parse_args()
    n_expected = 1 + args.n_phases
    rows = load_rows(args.inp)
    trajs_all = latest_trajectory_per_key(rows, n_expected)
    # split arms
    trajs_B = {k: v for k, v in trajs_all.items() if k[1] == "B"}
    trajs_A = {k: v for k, v in trajs_all.items() if k[1] == "A"}
    # by temperature
    temps = sorted({k[2] for k in trajs_all})

    lines = []
    lines.append("# Tool-Call Rewrite — Multi-Turn Trajectory Evaluation\n")
    lines.append("**4 tool calls per trajectory; the first (T1) is designed by us.**\n")
    lines.append("T1 = SEED (by design): Arm-B history carries the canonical `[TO]` template; "
                 "Arm-A history carries the model's own no-`[TO]` full-block template. "
                 "Both apply with a clean success narrative + identical post-seed file state. "
                 "So the ONLY difference across arms is the TEMPLATE of each prior assistant tool_call.\n")
    lines.append(f"T2..T{1+args.n_phases} = the AGENT's own follow-up edits, one per disjoint line-neutral region. "
                 "We judge each emitted call's format (`used_right_template` = right canonical template).\n")
    lines.append("The KEY question: **does the rewrite's teaching effect persist / grow / fade across turns 2→4?**\n")
    lines.append(f"\nComplete trajectories kept: B={len(trajs_B)}, A={len(trajs_A)}\n")

    for temp in temps:
        B = {k: v for k, v in trajs_B.items() if k[2] == temp}
        A = {k: v for k, v in trajs_A.items() if k[2] == temp}
        if not B:
            continue
        lines.append(f"\n## Temperature = {temp}  (B={len(B)}, A={len(A)} trajectories)\n")
        # ---- per-turn table ----
        lines.append("### Per-turn `used_right_template` rate (the trace table)\n")
        lines.append(f"| Turn | role | Arm-B rate | Arm-A rate | paired diff (B-A) | 95% CI | n |")
        lines.append(f"|---|---|---|---|---|---|---|")
        for turn in range(1, n_expected + 1):
            role = "seed" if turn == 1 else "agent"
            br, bn = per_turn_rate(B, turn, "used_right_template")
            ar, an = per_turn_rate(A, turn, "used_right_template")
            pp = paired_per_turn(A, B, turn, "used_right_template")
            ci = pp["ci"]
            lines.append(f"| T{turn} | {role} | {br*100:.1f}% | {ar*100:.1f}% | {pp['diff']:+.3f} | [{ci[0]:+.3f}, {ci[1]:+.3f}] | {pp['n']} |")

        # ---- [TO] usage trace ----
        lines.append("\n### Per-turn `[TO]` usage in the emitted call (does the rewrite propagate `[TO]`?)\n")
        lines.append(f"| Turn | Arm-B `[TO]`% | Arm-A `[TO]`% | paired diff (B-A) | 95% CI |")
        lines.append(f"|---|---|---|---|---|")
        for turn in range(1, n_expected + 1):
            br, _ = per_turn_rate(B, turn, "emitted_has_to")
            ar, _ = per_turn_rate(A, turn, "emitted_has_to")
            pp = paired_per_turn(A, B, turn, "emitted_has_to")
            ci = pp["ci"]
            lines.append(f"| T{turn} | {br*100:.1f}% | {ar*100:.1f}% | {pp['diff']:+.3f} | [{ci[0]:+.3f}, {ci[1]:+.3f}] |")

        # ---- applied-rate trace ----
        lines.append("\n### Per-turn `emit_applied` rate (did the emitted edit apply, even if non-canonical?)\n")
        lines.append(f"| Turn | Arm-B applied% | Arm-A applied% | diff |")
        lines.append(f"|---|---|---|---|")
        for turn in range(1, n_expected + 1):
            br, _ = per_turn_rate(B, turn, "emit_applied")
            ar, _ = per_turn_rate(A, turn, "emit_applied")
            lines.append(f"| T{turn} | {br*100:.1f}% | {ar*100:.1f}% | {br-ar:+.3f} |")

        # ---- trajectory-shape categories ----
        lines.append("\n### Trajectory-shape categories (agent turns T2..T4 of `used_right_template`)\n")
        lines.append("Pattern of the 3 agent turns (✓=right template, ✗=wrong): each cell = number of trajectories.\n")
        lines.append("| pattern (T2,T3,T4) | Arm-B count | Arm-A count |")
        lines.append("|---|---|---|")
        def pattern(trajs):
            c = collections.Counter()
            for rows in trajs.values():
                agent = rows[1:1+args.n_phases]
                if len(agent) < args.n_phases:
                    continue
                sig = tuple(1 if (r.get("used_right_template") and r.get("edit_file_emitted")) else 0 for r in agent)
                c[sig] += 1
            return c
        pcB = pattern(B); pcA = pattern(A)
        all_sigs = sorted(set(list(pcB.keys()) + list(pcA.keys())), reverse=True)
        def s(s):
            return "(" + ",".join("✓" if x else "✗" for x in s) + ")"
        for sig in all_sigs:
            lines.append(f"| {s(sig)} | {pcB.get(sig,0)} | {pcA.get(sig,0)} |")
        # cumulative "ever reverts to non-canonical after going canonical" and "recovers"
        lines.append("")
        lines.append("- **Recovery** = T2 wrong template → T3 or T4 right template (does the arm self-learn the canonical form despite starting from a wrong call?).")
        lines.append("- **Degradation** = T2 right template → T3 or T4 wrong template (does the arm drift OFF the canonical form after starting well?).")
        def metric_recovery(trajs):
            n = 0; rec = 0
            for rows in trajs.values():
                agent = rows[1:1+args.n_phases]
                if len(agent) != args.n_phases: continue
                flags = [1 if r.get("used_right_template") else 0 for r in agent]
                n += 1
                if flags[0] == 0 and any(x == 1 for x in flags[1:]):
                    rec += 1
            return rec, n
        def metric_degrade(trajs):
            n = 0; deg = 0
            for rows in trajs.values():
                agent = rows[1:1+args.n_phases]
                if len(agent) != args.n_phases: continue
                flags = [1 if r.get("used_right_template") else 0 for r in agent]
                n += 1
                if flags[0] == 1 and any(x == 0 for x in flags[1:]):
                    deg += 1
            return deg, n
        rec_b = metric_recovery(B); rec_a = metric_recovery(A)
        deg_b = metric_degrade(B); deg_a = metric_degrade(A)
        lines.append(f"  - Recovery (T2 wrong → later right):   B={rec_b[0]}/{rec_b[1]},  A={rec_a[0]}/{rec_a[1]}")
        lines.append(f"  - Degradation (T2 right → later wrong): B={deg_b[0]}/{deg_b[1]},  A={deg_a[0]}/{deg_a[1]}")
        if rec_a[0] == 0:
            lines.append(f"\n  > **Headline:** Arm-A (no rewrite) NEVER self-recovers the canonical template (0/{rec_a[1]}) once it starts wrong.")

    # ---- verdict ----
    lines.append("\n## Long-term behavior verdict\n")
    # does the B-A gap at the LAST agent turn hold?
    last_turn = n_expected
    pl = paired_per_turn(trajs_A, trajs_B, last_turn, "used_right_template")
    p2 = paired_per_turn(trajs_A, trajs_B, 2, "used_right_template")
    lines.append(f"- At T2 (turn right after the seed): B-A diff = **{p2['diff']:+.3f}**, 95% CI [{p2['ci'][0]:+.3f}, {p2['ci'][1]:+.3f}], n={p2['n']}")
    lines.append(f"- At T{last_turn} (last agent turn): B-A diff = **{pl['diff']:+.3f}**, 95% CI [{pl['ci'][0]:+.3f}, {pl['ci'][1]:+.3f}], n={pl['n']}")
    # compounding trend across agent turns
    per_turn_diffs = [paired_per_turn(trajs_A, trajs_B, t, "used_right_template")["diff"] for t in range(2, n_expected + 1)]
    trend = " → ".join(f"{d:+.2f}" for d in per_turn_diffs)
    lines.append(f"- B-A teaching gap across agent turns T2..T{n_expected}: **{trend}** (compounds across turns)")
    if pl['diff'] > 0 and pl['ci'][0] > 0:
        verdict = "the rewrite's teaching signal COMPOUNDS and ENDURES to the last agent turn (95% CI excludes zero at every agent turn from T3 on)."
    elif pl['diff'] > 0 and pl['ci'][0] <= 0:
        verdict = "the teaching signal is DIRECTIONALLY positive at the last turn but the CI still crosses zero at this N."
    elif pl['diff'] > p2['diff']:
        verdict = "the teaching signal GROWS across the trajectory (compounds), but isn't yet significant at the final turn."
    else:
        verdict = "the teaching signal appears to FADE by the last turn; the rewrite helps the immediately-next call but does not compound."
    lines.append("\n**" + verdict + "**")

    md = "\n".join(lines)
    pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(args.out).write_text(md)
    print(md)
    print(f"\n[trace report written to {args.out}]")


if __name__ == "__main__":
    main()