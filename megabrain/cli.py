"""megabrain CLI.

  megabrain index  [path]                      index/update a repo (incremental)
  megabrain query  [path] "task" [--compact]   one-shot code map
  megabrain get    [path] <file> [--symbol N]  pull code for navigation
  megabrain stats  [path]                      index stats
"""

import argparse
import sys
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="megabrain")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index")
    p.add_argument("path", nargs="?", default=".")

    p = sub.add_parser("query")
    p.add_argument("path")
    p.add_argument("task")
    p.add_argument("--compact", action="store_true")
    p.add_argument("--best", action="store_true", help="Haiku order-rerank of candidates (+~2s, never drops files)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("ask")
    p.add_argument("path")
    p.add_argument("question")
    p.add_argument("--best", action="store_true")
    p.add_argument("--no-map", action="store_true")
    p.add_argument("--docs", action="store_true",
                   help="explain docs (markdown) only, instead of code")

    p = sub.add_parser("get")
    p.add_argument("path")
    p.add_argument("file")
    p.add_argument("--symbol")

    p = sub.add_parser("stats")
    p.add_argument("path", nargs="?", default=".")

    a = ap.parse_args(argv)
    roots = [Path(p).resolve() for p in a.path.split(",")]
    root = roots[0]

    if a.cmd == "index":
        from .indexer import index_repo
        for r in roots:
            index_repo(r)
    elif a.cmd == "query":
        import json as _json

        from .query import render, search_multi
        res = search_multi(roots, a.task) if len(roots) > 1 else __import__("megabrain.query", fromlist=["search"]).search(roots[0], a.task, rerank=a.best)
        print(_json.dumps(res, indent=1) if a.json else render(res, compact=a.compact))
    elif a.cmd == "ask":
        from .ask import stream_ask
        stream_ask(root, a.question, rerank=a.best, show_map=not a.no_map,
                   docs_only=a.docs)
    elif a.cmd == "get":
        from .query import get_code
        print(get_code(root, a.file, a.symbol))
    elif a.cmd == "stats":
        from .store import Store
        s = Store(root)
        n_chunks = s.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        n_files = s.db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        n_syms = s.db.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        n_edges = s.db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        print(f"files={n_files} chunks={n_chunks} symbols={n_syms} edges={n_edges} "
              f"meta={s.get_meta('last_index')}")


if __name__ == "__main__":
    main()
