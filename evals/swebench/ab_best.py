"""A/B on a subset: stored baseline vs new code (no-rerank: recall; rerank: final)."""
import json, re, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from datasets import load_dataset
from megabrain.indexer import index_repo
from megabrain.query import search

HERE = Path(__file__).parent
sub_file = sys.argv[1] if len(sys.argv) > 1 else 'out/subset.json'
def gold_files(p):
    fs = re.findall(r"^diff --git a/(\S+)", p, re.M)
    return [f for f in dict.fromkeys(fs) if not (f.startswith(('tests/','test_')) or '/tests/' in f)]

subset = json.load(open(HERE/sub_file))
base = {r['instance_id']: r for r in map(json.loads, open(HERE/'out/results.jsonl')) if 'gold' in r}
ds = {r['instance_id']: r for r in load_dataset('princeton-nlp/SWE-bench_Lite', split='test')
      if r['instance_id'] in set(subset)}

ba={1:0,3:0,5:0,10:0}; nn={1:0,3:0,5:0,10:0}; nr={1:0,3:0,5:0}
for iid in subset:
    r = ds[iid]; g = gold_files(r['patch'])
    wt = HERE/'wt/snap2'
    subprocess.run(f"rm -rf {wt} && mkdir -p {wt}", shell=True, check=True)
    bare = HERE/'clones'/r['repo'].replace('/','__')
    subprocess.run(f"git --git-dir={bare} archive {r['base_commit']} | tar -x -C {wt}", shell=True, check=True)
    index_repo(wt, repo_name=r['repo'].split('/')[-1], quiet=True)
    res = search(wt, r['problem_statement'][:6000], rerank=False)
    rf_n = [t['file'] for t in res['tier1']] + [t['file'] for t in res['tier2']]
    res = search(wt, r['problem_statement'][:6000], rerank=True)
    rf_r = [t['file'] for t in res['tier1']] + [t['file'] for t in res['tier2']]
    brf = base[iid]['ranked']
    for k in ba:
        ba[k] += all(x in brf[:k] for x in g)
        nn[k] += all(x in rf_n[:k] for x in g)
    for k in nr: nr[k] += all(x in rf_r[:k] for x in g)
n=len(subset)
print(f"\n=== n={n}: {sub_file} ===")
print(f"  {'k':>3} {'baseline':>9} {'new(noLLM)':>11} {'new+rerank':>11}")
for k in (1,3,5):
    print(f"  @{k:<2} {ba[k]/n:>9.3f} {nn[k]/n:>11.3f} {nr[k]/n:>11.3f}")
print(f"  @10 {ba[10]/n:>9.3f} {nn[10]/n:>11.3f}  (recall ceiling)")
