"""Run the full tool-call-rewrite evaluation and write one JSONL row per
(trial, arm, temperature) call. Resumable: already-completed keys are skipped.

Usage:
  python -m AuroraCoder.evals.tool_call_rewrite_eval.run_eval \
      --replicates 6 --seed 1 --temps 0.7 --arms B,A_clean,A_nat \
      --out AuroraCoder/evals/tool_call_rewrite_eval/results/results.jsonl
"""
import argparse
import json
import os
import sys
import time
import pathlib

from AuroraCoder.evals.tool_call_rewrite_eval import (
    corpus, harness, llm as llm_mod,
)

PKG = pathlib.Path(__file__).resolve().parent


def load_done(out_path):
    done = set()
    if pathlib.Path(out_path).exists():
        for line in pathlib.Path(out_path).read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                done.add((r["trial_id"], r["arm"], r["temperature"]))
            except Exception:
                pass
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replicates", type=int, default=6)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--temps", type=str, default="0.7")
    ap.add_argument("--arms", type=str, default="B,A_clean,A_nat")
    ap.add_argument("--out", type=str, default=str(PKG / "results" / "results.jsonl"))
    ap.add_argument("--model", type=str, default=llm_mod.DEFAULT_MODEL)
    ap.add_argument("--limit-trials", type=int, default=0, help="0 = no limit")
    ap.add_argument("--workers", type=int, default=6, help="concurrent LLM calls")
    ap.add_argument("--modes", type=str, default="", help="comma-sep mode filter, e.g. F3 (empty=all)")
    args = ap.parse_args()

    temps = [float(x) for x in args.temps.split(",") if x.strip()]
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    done = load_done(args.out)
    print(f"already-done keys: {len(done)}; temps={temps}; arms={arms}; model={args.model}", flush=True)

    trials = corpus.generate_trials(corpus.prepare_pristine(), args.replicates, seed=args.seed)
    if args.modes.strip():
        want = {m.strip() for m in args.modes.split(",") if m.strip()}
        trials = [t for t in trials if t["mode"] in want]
    if args.limit_trials:
        trials = trials[: args.limit_trials]
    print(f"trials (after mode filter): {len(trials)} -> {len(trials)*len(arms)*len(temps)} planned calls", flush=True)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    client = llm_mod.get_client()
    out_fp = open(args.out, "a")
    lock = threading.Lock()

    jobs = []
    for trial in trials:
        for arm in arms:
            for temp in temps:
                if (trial["trial_id"], arm, temp) in done:
                    continue
                jobs.append((trial, arm, temp))
    print(f"pending jobs: {len(jobs)} with {args.workers} workers", flush=True)

    def run_one(job):
        trial, arm, temp = job
        row = harness.run_arm(trial, arm, client, model=args.model, temperature=temp)
        row["temperature"] = temp
        return row

    t0 = time.time()
    n_done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(run_one, j) for j in jobs]
        for fut in as_completed(futs):
            try:
                row = fut.result()
            except Exception as exc:
                print(f"  !! job crashed: {exc!r}", flush=True)
                continue
            with lock:
                out_fp.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                out_fp.flush()
                n_done += 1
                if n_done % 25 == 0:
                    el = time.time() - t0
                    print(f"  [{n_done}/{len(jobs)}] {row['trial_id']} {row['arm']} T={row['temperature']} "
                          f"used_right={row.get('used_right_template')} has_to={row.get('emitted_has_to')} "
                          f"err={row.get('llm_error')} elapsed={el:.0f}s", flush=True)
    out_fp.close()
    print(f"DONE. wrote {n_done} new rows to {args.out}", flush=True)


if __name__ == "__main__":
    main()