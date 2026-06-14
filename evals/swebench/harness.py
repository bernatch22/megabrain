"""SWE-bench Lite file-localization harness for megabrain.

Per instance: checkout base_commit (git worktree), incremental index,
query with problem_statement, score Acc@k (ALL gold files in top-k files)
and recall@k (any gold file) for k in {1,3,5,10}.

Usage:
  python3 harness.py --repos psf/requests,pallets/flask     # smoke subset
  python3 harness.py --all                                  # full 300
  python3 harness.py --all --resume                         # skip scored instances

Results appended to out/results.jsonl (resumable); summary printed at end.
Baselines (Acc@1/3/5): CodeRankEmbed 52.6/77.7/84.7 · SweRankEmbed-L 72.6/91.2/94.2
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

CLONES = HERE / "clones"
WT = HERE / "wt"
OUT = HERE / "out"
for d in (CLONES, WT, OUT):
    d.mkdir(parents=True, exist_ok=True)
RESULTS = OUT / "results.jsonl"  # may be retagged in main()


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True, **kw)


def gold_files(patch: str) -> list[str]:
    files = re.findall(r"^diff --git a/(\S+)", patch, re.M)
    return [f for f in dict.fromkeys(files)
            if not (f.startswith("tests/") or "/tests/" in f or f.startswith("test_"))]


def ensure_clone(repo: str) -> Path:
    d = CLONES / repo.replace("/", "__")
    if not d.exists():
        print(f"  cloning {repo} ...", flush=True)
        sh(f"git clone --bare -q https://github.com/{repo} {d}")
    return d


def checkout(repo: str, commit: str) -> Path:
    bare = ensure_clone(repo)
    wt = WT / "snap"
    if wt.exists():
        sh(f"rm -rf {wt}")
    wt.mkdir(parents=True)
    sh(f"git --git-dir={bare} archive {commit} | tar -x -C {wt}")
    return wt


def ranked_files(res: dict) -> list[str]:
    return [t["file"] for t in res["tier1"]] + [t["file"] for t in res["tier2"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--rerank", action="store_true")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    global RESULTS, WT
    if a.tag:
        RESULTS = OUT / f"results_{a.tag}.jsonl"
        WT = HERE / f"wt_{a.tag}"
        WT.mkdir(exist_ok=True)

    from datasets import load_dataset

    from megabrain.indexer import index_repo
    from megabrain.query import search

    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    rows = list(ds)
    if a.repos:
        keep = set(a.repos.split(","))
        rows = [r for r in rows if r["repo"] in keep]
    if a.limit:
        rows = rows[:a.limit]
    rows.sort(key=lambda r: (r["repo"], r["base_commit"]))  # maximize cache reuse

    done = set()
    if a.resume and RESULTS.exists():
        done = {r["instance_id"] for r in map(json.loads, open(RESULTS)) if "gold" in r}

    for i, r in enumerate(rows):
        if r["instance_id"] in done:
            continue
        gold = gold_files(r["patch"])
        if not gold:
            continue
        t0 = time.time()
        try:
            wt = checkout(r["repo"], r["base_commit"])
            idx = index_repo(wt, repo_name=r["repo"].split("/")[-1], quiet=True)
            res = search(wt, r["problem_statement"][:6000], rerank=a.rerank)
            rf = ranked_files(res)
        except Exception as e:
            print(f"  ERROR {r['instance_id']}: {e}", flush=True)
            with open(RESULTS, "a") as f:
                f.write(json.dumps({"instance_id": r["instance_id"], "error": str(e)}) + "\n")
            continue
        rec = {
            "instance_id": r["instance_id"], "repo": r["repo"], "gold": gold,
            "ranked": rf[:30], "seconds": round(time.time() - t0, 1),
            "index_cost": idx["embed_cost_usd"], "changed": idx["changed"],
        }
        with open(RESULTS, "a") as f:
            f.write(json.dumps(rec) + "\n")
        hit1 = all(g in rf[:1] for g in gold)
        print(f"[{i+1}/{len(rows)}] {r['instance_id']} gold={len(gold)} "
              f"acc@1={'Y' if hit1 else 'n'} {rec['seconds']}s ${rec['index_cost']}", flush=True)

    # summary
    recs = [json.loads(l) for l in open(RESULTS) if "gold" in l]
    recs = [r for r in recs if "error" not in r]
    n = len(recs)
    if not n:
        return
    print(f"\n=== {n} instances ===")
    for k in (1, 3, 5, 10):
        acc = sum(all(g in r["ranked"][:k] for g in r["gold"]) for r in recs) / n
        rec_k = sum(any(g in r["ranked"][:k] for g in r["gold"]) for r in recs) / n
        print(f"  Acc@{k}={acc:.3f}  recall@{k}={rec_k:.3f}")
    cost = sum(r["index_cost"] for r in recs)
    print(f"  total index cost=${cost:.3f}  avg={sum(r['seconds'] for r in recs)/n:.1f}s/instance")


if __name__ == "__main__":
    main()
