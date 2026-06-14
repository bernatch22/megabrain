"""Re-run only the zero-citation instances with the (now retry-equipped) ask,
to confirm the API-throttle failures recover. Rewrites results_ask.jsonl."""
import json, re, subprocess, sys
from pathlib import Path
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent))
from datasets import load_dataset
from megabrain.ask import ask, cited_files
from megabrain.indexer import index_repo

RES = HERE / "out/results_ask.jsonl"
recs = [json.loads(l) for l in open(RES) if '"gold"' in l]
by_id = {r["instance_id"]: r for r in recs}
failed = [iid for iid, r in by_id.items() if not r["cited"]]
print(f"re-running {len(failed)} zero-cite instances")
ds = {r["instance_id"]: r for r in load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
      if r["instance_id"] in set(failed)}
WT = HERE / "wt_ask"
for i, iid in enumerate(failed):
    r = ds[iid]
    wt = WT / "snap"
    subprocess.run(f"rm -rf {wt} && mkdir -p {wt}", shell=True, check=True)
    bare = HERE / "clones" / r["repo"].replace("/", "__")
    subprocess.run(f"git --git-dir={bare} archive {r['base_commit']} | tar -x -C {wt}", shell=True, check=True)
    index_repo(wt, repo_name=r["repo"].split("/")[-1], quiet=True)
    out = ask(wt, r["problem_statement"][:6000])
    cites = cited_files(out)
    by_id[iid]["cited"] = cites
    print(f"[{i+1}/{len(failed)}] {iid} ncited={len(cites)} cite@1={'Y' if cites and cites[0] in by_id[iid]['gold'] else 'n'}", flush=True)

with open(RES, "w") as f:
    for r in by_id.values():
        f.write(json.dumps(r) + "\n")
n = len(recs); real = [r for r in by_id.values() if r["cited"]]
print(f"\n=== full {n}, {n-len(real)} still empty ===")
for k in (1, 3, 5, 10):
    print(f"  Acc@{k}={sum(all(g in r['cited'][:k] for g in r['gold']) for r in by_id.values())/n:.3f}")
