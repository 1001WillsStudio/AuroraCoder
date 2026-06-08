"""One-off helper: build a tiny 2-instance JSONL for a SWE-bench test run.

Picks two instances from a small repo (psf/requests) in SWE-bench Lite so
host-side clones are fast. Writes data/swe-bench-test.jsonl.
"""
import json
from pathlib import Path

import httpx

FIELDS = ("repo", "instance_id", "base_commit", "problem_statement",
          "patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS",
          "version", "environment_setup_commit", "hints_text")


PREFERRED_REPO = "psf/requests"  # small repo → fast shallow clone


def fetch_small_instances(n: int = 2) -> list[dict]:
    collected: list[dict] = []
    for offset in range(0, 300, 100):
        resp = httpx.get(
            "https://datasets-server.huggingface.co/rows",
            params={
                "dataset": "princeton-nlp/SWE-bench_Lite",
                "config": "default",
                "split": "test",
                "offset": offset,
                "length": 100,
            },
            timeout=120,
        )
        resp.raise_for_status()
        for row in resp.json()["rows"]:
            r = row["row"]
            if r.get("repo") == PREFERRED_REPO:
                collected.append({k: r.get(k) for k in FIELDS})
                if len(collected) >= n:
                    return collected
    return collected


def main() -> None:
    out = Path("data/swe-bench-test.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    instances = fetch_small_instances(2)
    with out.open("w", encoding="utf-8") as f:
        for inst in instances:
            f.write(json.dumps(inst, ensure_ascii=False) + "\n")
    print(f"Wrote {len(instances)} instances to {out.resolve()}")
    for inst in instances:
        print(" -", inst["instance_id"], "|", inst["repo"], "|", inst["base_commit"])


if __name__ == "__main__":
    main()
