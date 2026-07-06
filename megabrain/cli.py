"""megabrain CLI.

  megabrain index  [path]                      index/update a repo (incremental)
  megabrain query  [path] "task" [--compact]   one-shot code map
  megabrain ask    [path] "question"           explained walkthrough
  megabrain get    [path] <file> [--symbol N]  pull code for navigation
  megabrain serve-api [path] --port N          long-running JSON API (warm state)
  megabrain stats  [path]                      index stats

PATH-SCOPE: for query/ask/get, `path` may be the repo root OR a sub-path inside
it (e.g. ~/repo/src/dispatch). megabrain auto-detects the repo root (the nearest
ancestor with .megabrain/db.sqlite) and scopes retrieval to files under the
sub-path. The repo root itself behaves exactly as before (no filter).
"""

import argparse
import logging
import os
from pathlib import Path


def main(argv=None):
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("MEGABRAIN_DEBUG") else logging.INFO,
        format="%(message)s")
    ap = argparse.ArgumentParser(prog="megabrain")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--force", action="store_true",
                   help="re-embed every file, ignoring the sha cache (e.g. after an embed-model change)")
    p.add_argument("--exclude", action="append", default=[], metavar="PATTERN",
                   help="skip a dir name or a glob/path (repeatable, or comma-separated); "
                        "merged with built-ins and .megabrainignore")

    p = sub.add_parser("query")
    p.add_argument("path")
    p.add_argument("task")
    p.add_argument("--compact", action="store_true")
    p.add_argument("--full", action="store_true",
                   help="include RELATED best-chunk code bodies (default renders "
                        "RELATED as a map: file, match span, symbols — ~60%% fewer tokens)")
    p.add_argument("--best", action="store_true", help="LLM order-rerank of candidates (+~2s, never drops files)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("ask")
    p.add_argument("path")
    p.add_argument("question")
    p.add_argument("--best", action="store_true")
    p.add_argument("--no-map", action="store_true")
    p.add_argument("--docs", action="store_true",
                   help="explain docs (markdown) only, instead of code")
    p.add_argument("--with-docs", action="store_true",
                   help="explain code AND docs together (default is code only)")

    p = sub.add_parser("get")
    p.add_argument("path")
    p.add_argument("file")
    p.add_argument("--symbol")

    p = sub.add_parser("chunks",
                       help="every chunk of one file, scored for a query, with a selected flag (JSON)")
    p.add_argument("path")
    p.add_argument("file")
    p.add_argument("query")

    p = sub.add_parser("serve-api")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--port", type=int, default=2134)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--cors", help="allowed browser origin, e.g. https://docs.example.com")
    p.add_argument("--no-llm", action="store_true", help="disable the /ask endpoint")
    p.add_argument("--token", default=os.environ.get("MEGABRAIN_API_TOKEN"),
                   help="require `Authorization: Bearer <token>` on every request except "
                        "/health (default: $MEGABRAIN_API_TOKEN; recommended off-localhost)")

    p = sub.add_parser("stats")
    p.add_argument("path", nargs="?", default=".")

    a = ap.parse_args(argv)
    # index/serve-api/stats take repo roots verbatim (index may have no db yet).
    # query/ask/get support PATH-SCOPE: each comma-separated token may be a repo
    # root OR a sub-path inside one — resolve_root() finds the .megabrain root and
    # the sub-path used to scope retrieval to files under it.
    raw = [Path(p).resolve() for p in a.path.split(",")]
    root = raw[0]
    if len(raw) > 1 and a.cmd not in ("index", "query"):
        ap.error(f"`{a.cmd}` takes a single path — comma-separated multi-path "
                 f"applies to `index` and `query` only")

    if a.cmd == "index":
        from .indexer import index_repo
        exclude = [x for item in a.exclude for x in item.split(",") if x.strip()]
        for r in raw:
            index_repo(r, force=a.force, exclude=exclude)
    elif a.cmd == "query":
        import json as _json

        from .indexer import maybe_reindex
        from .query import render, search, search_multi
        from .store import resolve_root
        scoped = [resolve_root(p) for p in raw]           # [(root, subpath), …]
        roots = [r for r, _ in scoped]
        pfs = [sp or None for _, sp in scoped]
        for r in dict.fromkeys(roots):     # answers match disk (60s TTL, fail-open)
            maybe_reindex(r)
        res = (search_multi(roots, a.task, path_filters=pfs) if len(roots) > 1
               else search(roots[0], a.task, rerank=a.best, path_filter=pfs[0]))
        print(_json.dumps(res, indent=1) if a.json
              else render(res, compact=a.compact, related_code=a.full))
    elif a.cmd == "ask":
        from .ask import stream_ask
        from .indexer import maybe_reindex
        from .store import resolve_root
        r0, sp = resolve_root(root)
        maybe_reindex(r0)                  # answers match disk (60s TTL, fail-open)
        stream_ask(r0, a.question, rerank=a.best, show_map=not a.no_map,
                   docs_only=a.docs, path_filter=sp or None,
                   include_docs=a.with_docs)
    elif a.cmd == "get":
        from .query import get_code
        from .store import resolve_root
        r0, sp = resolve_root(root)
        # a bare file arg under a sub-path is joined onto the sub-path so
        # `megabrain get ~/repo/src dispatch.ts` finds src/dispatch.ts.
        rel = a.file
        if sp and not (Path(r0) / rel).exists() and (Path(r0) / sp / rel).exists():
            rel = (Path(sp) / rel).as_posix()
        print(get_code(r0, rel, a.symbol))
    elif a.cmd == "chunks":
        import json as _json

        from .indexer import maybe_reindex
        from .query import chunks_for_file_root
        from .store import resolve_root
        r0, sp = resolve_root(root)
        maybe_reindex(r0)
        rel = a.file
        if sp and not (Path(r0) / rel).exists() and (Path(r0) / sp / rel).exists():
            rel = (Path(sp) / rel).as_posix()
        print(_json.dumps(chunks_for_file_root(r0, rel, a.query, path_filter=sp or None), indent=1))
    elif a.cmd == "serve-api":
        from .serve import serve
        serve(root, port=a.port, host=a.host, cors=a.cors, enable_llm=not a.no_llm,
              token=a.token)
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
