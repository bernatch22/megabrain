"""Phase 7 gate (part 2): scale sanity on vscode-js-debug (417 TS files, 134K lines).
Gates: query p50 < 1.5s cold-ish, sane bundle for known features, stats consistent.
Run: python3 tests/test_scale.py"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from megabrain.retrieval.bundle import search
from megabrain.retrieval.render import render
from megabrain.store import Store

REPO = Path.home() / "vscode-js-debug"

QUERIES = [
    ("how are breakpoints set and resolved against source maps",
     ["breakpoint"]),
    ("where is the CDP connection to the browser established",
     ["cdp", "connection", "launch", "browser"]),
    ("how does the debug adapter handle variable inspection / scopes",
     ["variable", "scope"]),
    ("console output formatting and object previews",
     ["console", "preview", "output"]),
]


def main():
    s = Store(REPO)
    n_chunks = s.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    n_files = s.db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    n_edges = s.db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    print(f"index: files={n_files} chunks={n_chunks} edges={n_edges}")
    assert n_files > 350 and n_chunks > 700, "index looks incomplete"

    lats = []
    for q, expect_terms in QUERIES:
        t0 = time.time()
        res = search(REPO, q)
        dt = time.time() - t0
        lats.append(dt)
        top = [t["file"] for t in res["tier1"]] + [t["file"] for t in res["tier2"]][:6]
        hit = any(any(term in f.lower() for term in expect_terms) for f in top)
        out = render(res, compact=True)
        print(f"  {dt*1000:>5.0f}ms top1={top[0]} term-hit={hit} map={len(out)}chars")
        assert hit, f"no expected term in tier1 for: {q} -> {top}"
    lats.sort()
    p50 = lats[len(lats)//2]
    print(f"p50={p50*1000:.0f}ms")
    assert p50 < 1.5
    print("PHASE 7 GATE (scale): PASSED")


if __name__ == "__main__":
    main()
