"""SWE-bench Lite localization scored via `megabrain ask`.

For each instance: checkout base_commit, index (warm cache), run ask, and
score Acc@k on the FILES THE EXPLANATION CITES (in citation order) — i.e.
"when megabrain ask explains the fix area, does it cite the file that the
real patch edits?" Reuses warm clones; separate worktree (wt_ask); resumable.

Usage: python3 ask_eval.py [--limit N]
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent))

OUT = HERE / "out"
WT = HERE / "wt_ask"
WT.mkdir(parents=True, exist_ok=True)


def gold_files(patch):
    fs = re.findall(r"^diff --git a/(\S+)", patch, re.M)
    return [f for f in dict.fromkeys(fs)
            if not (f.startswith(("tests/", "test_")) or "/tests/" in f)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    from datasets import load_dataset

    from megabrain.ask import ask, cited_files
    from megabrain.indexer import index_repo

    results = OUT / "results_ask.jsonl"
    done = set()
    if results.exists():
        done = {json.loads(l)["instance_id"] for l in open(results) if '"gold"' in l}

    # only instances the baseline already cloned/scored (warm)
    scored = [json.loads(l) for l in open(OUT / "results.jsonl") if '"gold"' in l]
    want = {r["instance_id"] for r in scored}
    ds = [r for r in load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
          if r["instance_id"] in want and r["instance_id"] not in done]
    ds.sort(key=lambda r: (r["repo"], r["base_commit"]))
    if a.limit:
        ds = ds[:a.limit]

    for i, r in enumerate(ds):
        gold = gold_files(r["patch"])
        if not gold:
            continue
        t0 = time.time()
        try:
            wt = WT / "snap"
            subprocess.run(f"rm -rf {wt} && mkdir -p {wt}", shell=True, check=True)
            bare = HERE / "clones" / r["repo"].replace("/", "__")
            subprocess.run(f"git --git-dir={bare} archive {r['base_commit']} | tar -x -C {wt}",
                           shell=True, check=True)
            index_repo(wt, repo_name=r["repo"].split("/")[-1], quiet=True)
            out = ask(wt, r["problem_statement"][:6000])
            cites = cited_files(out)
        except Exception as e:
            print(f"  ERROR {r['instance_id']}: {e}", flush=True)
            continue
        rec = {"instance_id": r["instance_id"], "repo": r["repo"], "gold": gold,
               "cited": cites, "seconds": round(time.time() - t0, 1)}
        with open(results, "a") as f:
            f.write(json.dumps(rec) + "\n")
        hit = "Y" if (cites and cites[0] in gold) else "n"
        print(f"[{i+1}/{len(ds)}] {r['instance_id']} cite@1={hit} ncited={len(cites)} {rec['seconds']}s", flush=True)

    recs = [json.loads(l) for l in open(results) if '"gold"' in l]
    n = len(recs)
    if n:
        print(f"\n=== ask: {n} instances ===")
        for k in (1, 3, 5, 10):
            acc = sum(all(g in r["cited"][:k] for g in r["gold"]) for r in recs) / n
            rec_k = sum(any(g in r["cited"][:k] for g in r["gold"]) for r in recs) / n
            print(f"  Acc@{k}={acc:.3f}  recall@{k}={rec_k:.3f}")
        import statistics
        print(f"  median {statistics.median(r['seconds'] for r in recs):.1f}s/instance")


if __name__ == "__main__":
    main()
