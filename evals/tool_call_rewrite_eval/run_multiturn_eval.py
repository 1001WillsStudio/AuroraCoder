"""Run the multi-turn trajectory evaluation. Resumable at the trajectory level:
a (trial_id, arm, temperature) trajectory is re-run from scratch only if it isn't
complete (i.e. it lacks turns 1..n+1, or any turn has an llm_error). Trajectories
are parallelised across (trial, arm) pairs; within a trajectory turns are serial.

Usage:
  python -m AuroraCoder.evals.tool_call_rewrite_eval.run_multiturn_eval \
      --reps 4 --n-phases 3 --arms A,B --temps 0.7 --workers 6 \
      --out AuroraCoder/evals/tool_call_rewrite_eval/results/multiturn.jsonl
"""
import argparse
import collections
import json
import pathlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from AuroraCoder.evals.tool_call_rewrite_eval import (
    corpus, multiturn, llm as llm_mod,
)

PKG = pathlib.Path(__file__).resolve().parent


def build_trials(reps, n_phases, seed_mode="F3"):
    rels = corpus.prepare_pristine()
    trials = []
    for rel in rels:
        src = corpus.PRISTINE.parent / rel
        for rep in range(reps):
            t = multiturn.make_trajectory_trial(src, rel, n_phases=n_phases,
                                               seed_mode=seed_mode, rep=rep, rng_seed=0)
            if t is not None:
                trials.append(t)
    return trials


def load_existing(out_path):
    groups = collections.defaultdict(list)
    if pathlib.Path(out_path).exists():
        for line in pathlib.Path(out_path).read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                key = (r["trial_id"], r["arm"], r.get("temperature"))
                groups[key].append(r)
            except Exception:
                pass
    return groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=4)
    ap.add_argument("--n-phases", type=int, default=3)
    ap.add_argument("--seed-mode", type=str, default="F3")
    ap.add_argument("--arms", type=str, default="A,B")
    ap.add_argument("--temps", type=str, default="0.7")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--model", type=str, default=llm_mod.DEFAULT_MODEL)
    ap.add_argument("--out", type=str, default=str(PKG / "results" / "multiturn.jsonl"))
    args = ap.parse_args()

    temps = [float(x) for x in args.temps.split(",") if x.strip()]
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    groups = load_existing(args.out)
    # build the set of trajectories to (re)run
    trials = build_trials(args.reps, args.n_phases, seed_mode=args.seed_mode)
    print(f"built {len(trials)} trajectories (reps={args.reps}, n_phases={args.n_phases})", flush=True)
    jobs = []  # (trial, arm, temp)
    for t in trials:
        for arm in arms:
            for temp in temps:
                key = (t["trial_id"], arm, temp)
                prev = groups.get(key)
                complete = prev and len(prev) == (1 + args.n_phases) and \
                    all(not (r.get("llm_error") or r.get("emit_error")) for r in prev)
                if not complete:
                    jobs.append((t, arm, temp))
    print(f"pending trajectories: {len(jobs)}; skipping {len(trials)*len(arms)*len(temps)-len(jobs)} complete", flush=True)

    if not jobs:
        print("nothing to do.", flush=True)
        return

    client = llm_mod.get_client()
    lock = threading.Lock()
    out_fp = open(args.out, "a")
    n_done = 0
    t0 = time.time()

    def run_one(job):
        trial, arm, temp = job
        rows = multiturn.run_trajectory(trial, arm, client, model=args.model,
                                        temperature=temp, n_phases=args.n_phases)
        return (trial["trial_id"], arm, temp, rows)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(run_one, j) for j in jobs]
        for fut in as_completed(futs):
            try:
                tid, arm, temp, rows = fut.result()
            except Exception as exc:
                print(f"  !! trajectory crashed: {exc!r}", flush=True)
                continue
            with lock:
                # overwrite this trajectory: drop existing rows for the key first
                # (rewrite the whole file is costly; instead append new rows and
                #  let the report mark superseded rows. We tag a fresh run_token.)
                token = f"{int(time.time()*1000)}"
                for r in rows:
                    rr = dict(r)
                    rr["temperature"] = temp
                    rr["run_token"] = token
                    out_fp.write(json.dumps(rr, ensure_ascii=False, default=str) + "\n")
                out_fp.flush()
                n_done += 1
                if n_done % 10 == 0:
                    el = time.time() - t0
                    print(f"  [{n_done}/{len(jobs)}] {tid} {arm} T={temp} turns={len(rows)} elapsed={el:.0f}s", flush=True)
    out_fp.close()
    print(f"DONE. wrote {n_done} new trajectories to {args.out}", flush=True)
    print("NOTE: re-run trajectories append a new run_token; the report keeps the "
          "latest complete trajectory per (trial,arm,temp).", flush=True)


if __name__ == "__main__":
    main()