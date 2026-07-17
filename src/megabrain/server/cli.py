"""megabrain CLI.

  megabrain index  [path]                      index/update a repo (incremental)
  megabrain search [path] "task" [--compact]   one-shot code map (`query` = alias)
  megabrain ask    [path] "question"           explained walkthrough
  megabrain get    [path] <file> [--symbol N]  pull code for navigation
  megabrain serve    [path] --port N           studio web UI + JSON API (warm state)
  megabrain serve-api [path] --port N          JSON API only, no UI (warm state)
  megabrain stats  [path]                      index stats
  megabrain repos                              every repo indexed on this machine

PATH-SCOPE: for search/ask/get, `path` may be the repo root OR a sub-path inside
it (e.g. ~/repo/src/dispatch). megabrain auto-detects the repo root (the nearest
ancestor with .megabrain/db.sqlite) and scopes retrieval to files under the
sub-path. The repo root itself behaves exactly as before (no filter).
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from ..errors import MegabrainError


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
    p.add_argument("--warm-flows", nargs="?", const=6, default=None, type=int,
                   metavar="N",
                   help="OPT-IN: after indexing, discover the system's main workflows "
                        "and pre-cache them as flows — N research asks (default 6), "
                        "so the flow cache starts full instead of building up lazily")
    p.add_argument("--scan", action="store_true", dest="index_scan",
                   help="print the scan census (what will index + what's skipped and "
                        "why: .gitignore/vendored/generated), THEN index honoring those "
                        "smart filters")
    p.add_argument("--dry-run", action="store_true",
                   help="census only — alias of `megabrain scan`: show what WOULD index, "
                        "no embedding, no writes")

    p = sub.add_parser("scan",
                       help="index-intelligence census: what WOULD index + every "
                            "candidate skipped with the reason "
                            "(.gitignore/vendored/generated/too-big). No indexing.")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--json", action="store_true")
    p.add_argument("--write", action="store_true",
                   help="write the proposed .megabrainignore (deterministic skips only)")

    # `search` is the primary retrieval verb; `query` is its hidden pre-0.10
    # alias (same flags, same dispatch) so muscle memory and scripts keep working.
    def _add_search_args(p):
        p.add_argument("path")
        p.add_argument("task")
        p.add_argument("--compact", action="store_true")
        p.add_argument("--prune", action="store_true",
                       help="no-LLM noise pruning: a flat relevance-ranked list of only "
                            "the signal chunks ([id] file:lines · score + code), noise dropped")
        p.add_argument("--rerank", action="store_true",
                       help="add the LLM rerank on top of --prune: drop vocabulary-only "
                            "matches (tests/evals) and reorder (~1-2s, fails open to the "
                            "deterministic list; model: $MEGABRAIN_RERANK_MODEL)")
        p.add_argument("--full", action="store_true",
                       help="include RELATED best-chunk code bodies (default renders "
                            "RELATED as a map: file, match span, symbols — ~60%% fewer tokens)")
        p.add_argument("--json", action="store_true")
    _add_search_args(sub.add_parser("search",
                                    help="one-shot retrieval: CORE code + RELATED map "
                                         "(--prune for the flat signal-only list)"))
    _add_search_args(sub.add_parser("query", help=argparse.SUPPRESS))

    p = sub.add_parser("ask")
    p.add_argument("path")
    p.add_argument("question")
    p.add_argument("--no-map", action="store_true")
    p.add_argument("--docs", action="store_true",
                   help="explain docs (markdown) only, instead of code")
    p.add_argument("--with-docs", action="store_true",
                   help="explain code AND docs together (default is code only)")
    p.add_argument("--agents", action="store_true",
                   help="force the multi-agent fan-out (plan → parallel sub-agents → synthesis); "
                        "default is AUTO — broad questions fan out, scoped ones stay single-agent")
    p.add_argument("--no-agents", action="store_true",
                   help="never fan out — single-agent ask even for broad questions")

    p = sub.add_parser("get")
    p.add_argument("path")
    p.add_argument("file")
    p.add_argument("--symbol")

    p = sub.add_parser("chunks",
                       help="every chunk of one file, scored for a query, with a selected flag (JSON)")
    p.add_argument("path")
    p.add_argument("file")
    p.add_argument("query")

    # serve-api = JSON API ONLY; serve = the same API + the studio web UI at /.
    # Same options either way — the only difference is whether the UI is mounted.
    def _add_serve_args(p):
        p.add_argument("path", nargs="?", default=".")
        p.add_argument("--port", type=int, default=2134)
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--cors", help="allowed browser origin, e.g. https://docs.example.com")
        p.add_argument("--no-llm", action="store_true", help="disable the /ask endpoint")
        p.add_argument("--token", default=os.environ.get("MEGABRAIN_API_TOKEN"),
                       help="require `Authorization: Bearer <token>` on every request except "
                            "/health (default: $MEGABRAIN_API_TOKEN; recommended off-localhost)")
    _add_serve_args(sub.add_parser("serve-api",
                                   help="long-running JSON API only (warm state, no UI)"))
    _add_serve_args(sub.add_parser("serve",
                                   help="the studio web UI at / + the JSON API (warm state)"))

    p = sub.add_parser("install",
                       help="register the MCP server with your AI coding assistants "
                            "(Claude Code, Codex, Antigravity, Cursor, Windsurf, "
                            "Gemini CLI) — detected automatically")
    p.add_argument("--platform", help="only this one (default: every platform detected)")
    p.add_argument("--list", action="store_true", dest="list_only",
                   help="show what's detected and where, change nothing")
    p.add_argument("--remove", action="store_true",
                   help="unregister megabrain instead of registering it")

    p = sub.add_parser("stats")
    p.add_argument("path", nargs="?", default=".")

    sub.add_parser("repos",
                   help="list every repo indexed on this machine (the global "
                        "registry at ~/.megabrain/registry.json; entries whose "
                        "index vanished are dropped automatically)")

    p = sub.add_parser("forge",
                       help="detect uncovered file types and LLM-generate a chunking "
                            "strategy for each, validated against every matching file "
                            "(exact line partition) before install")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--ext", help="forge one extension only, e.g. --ext .toml")
    p.add_argument("--list", action="store_true", dest="list_only",
                   help="detection census only — no LLM call")
    p.add_argument("--dry-run", action="store_true",
                   help="generate + validate but do not install or reindex")
    p.add_argument("--specialize", action="store_true",
                   help="census of ALREADY-covered file types the built-in chunks "
                        "poorly (data tables, blobs). NOTE: LLM-generated "
                        "specialization was removed (it lost to a deterministic "
                        "recipe); write the strategy into .megabrain/strategies/ by "
                        "hand and gate it with the Python API megabrain.forge."
                        "specialize.gate_strategy(). This flag now only lists opportunities.")

    p = sub.add_parser("flows",
                       help="list this repo's cached ask flows (self-caching workflow "
                            "retrieval); --clear drops them all; --warm N pre-caches "
                            "the system's main workflows via research asks")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--clear", action="store_true")
    p.add_argument("--warm", nargs="?", const=6, default=None, type=int, metavar="N")
    p.add_argument("--refresh", action="store_true",
                   help="re-ask stale flows against the current code (UPDATE, not just "
                        "expire) — reindex first so sha changes are seen")
    p.add_argument("--enable", action="store_true",
                   help="turn the flow-cache mode ON for this repo (off by default)")
    p.add_argument("--disable", action="store_true", help="turn the mode OFF")

    p = sub.add_parser("trust",
                       help="approve this repo's .megabrain/strategies/*.py (records "
                            "their sha in ~/.megabrain/trust.json so indexing loads them)")
    p.add_argument("path", nargs="?", default=".")

    a = ap.parse_args(argv)
    # index/serve-api/serve/stats take repo roots verbatim (index may have no db yet).
    # query/ask/get support PATH-SCOPE: each comma-separated token may be a repo
    # root OR a sub-path inside one — resolve_root() finds the .megabrain root and
    # the sub-path used to scope retrieval to files under it.
    # `install` and `repos` are machine-level (assistant configs / the global
    # registry), not repo-level — the two verbs that take no path.
    raw = [Path(p).resolve() for p in a.path.split(",")] if hasattr(a, "path") else []
    root = raw[0] if raw else None
    if len(raw) > 1 and a.cmd not in ("index", "search", "query"):
        ap.error(f"`{a.cmd}` takes a single path — comma-separated multi-path "
                 f"applies to `index` and `search` only")

    # THE one CLI catch site: engine errors print as one line + exit 2 — never
    # a raw traceback at the user. MEGABRAIN_DEBUG=1 re-raises for developers.
    try:
        _dispatch(a, raw, root)
    except MegabrainError as e:
        if os.environ.get("MEGABRAIN_DEBUG"):
            raise
        print(f"megabrain: {e}", file=sys.stderr)
        raise SystemExit(2) from None


def _scan(root: Path) -> dict:
    from .. import app
    return app.scan(root)


def _render_scan(rep: dict) -> str:
    """Human census: totals, by-extension, top dirs, and the flagged list
    grouped by reason (why each file was skipped) + the proposed ignore."""
    L = [f'# scan — {rep["would_index"]} files would index']
    if rep["by_ext"]:
        L.append("  by ext: " + "  ".join(f'{e} {n}' for e, n in
                                          list(rep["by_ext"].items())[:12]))
    if rep["top_dirs"]:
        L.append("  top dirs:")
        for d in rep["top_dirs"][:8]:
            L.append(f'    {d["dir"]:<24} {d["files"]:>5} files  '
                     f'{d["bytes"] // 1024:>6} KB')
    flagged = rep["flagged"]
    if flagged:
        by_reason: dict[str, int] = {}
        for f in flagged:
            by_reason[f["reason"]] = by_reason.get(f["reason"], 0) + 1
        L.append(f'\n# skipped {len(flagged)} candidates: '
                 + ", ".join(f"{n} {r}" for r, n in sorted(by_reason.items())))
        for f in flagged[:20]:
            L.append(f'    [{f["reason"]:<10}] {f["path"]}')
        if len(flagged) > 20:
            L.append(f'    … +{len(flagged) - 20} more')
    if rep["proposed_ignore"]:
        L.append("\n# proposed .megabrainignore (megabrain scan --write to apply):")
        L.append("".join(f"    {ln}\n" for ln in rep["proposed_ignore"].splitlines()))
    return "\n".join(L)


def _dispatch(a, raw: list[Path], root: Path) -> None:
    if a.cmd == "index":
        import json as _json

        from .. import app
        exclude = [x for item in a.exclude for x in item.split(",") if x.strip()]
        if a.dry_run:                     # census only — alias of `scan`
            for r in raw:
                print(_render_scan(_scan(r)))
            return
        for r in raw:
            if a.index_scan:              # show the census, then index with the filters
                print(_render_scan(_scan(r)))
            print(_json.dumps(app.index(r, force=a.force, exclude=exclude,
                                        scan_filters=a.index_scan), indent=1))
            if a.warm_flows:
                from ..ask.warmup import warm_flows
                print(_json.dumps(warm_flows(r, limit=a.warm_flows), indent=1))
    elif a.cmd == "scan":
        import json as _json
        rep = _scan(root)
        if a.write and rep["proposed_ignore"]:
            from ..server.http import _merge_ignore
            _merge_ignore(root, rep["proposed_ignore"])
            print(f"# wrote proposed skips to {root}/.megabrainignore\n")
        print(_json.dumps(rep, indent=1) if a.json else _render_scan(rep))
    elif a.cmd in ("search", "query"):     # query = pre-0.10 alias
        import json as _json

        from .. import app
        from ..retrieval.render import render, render_pruned
        from ..storage.store import resolve_root
        scoped = [resolve_root(p) for p in raw]           # [(root, subpath), …]
        roots = [r for r, _ in scoped]
        pfs = [sp or None for _, sp in scoped]
        if getattr(a, "prune", False) or getattr(a, "rerank", False):
            res = app.prune(roots[0], a.task, path_filter=pfs[0],
                            with_text=not a.compact,
                            llm_rerank=getattr(a, "rerank", False))
            print(_json.dumps(res, indent=1) if a.json
                  else render_pruned(res, with_text=not a.compact))
        elif len(roots) > 1:
            res = app.query_multi(roots, a.task, path_filters=pfs)
            print(_json.dumps(res, indent=1) if a.json
                  else render(res, compact=a.compact, related_code=a.full))
        else:
            res = app.query(roots[0], a.task, path_filter=pfs[0])
            print(_json.dumps(res, indent=1) if a.json
                  else render(res, compact=a.compact, related_code=a.full))
    elif a.cmd == "ask":
        # ask STREAMS on the CLI (token-by-token) — it drives stream_events
        # directly rather than app.ask (which is the buffered collector); the
        # reindex pre-step is applied here to match the other verbs.
        from .. import app
        from ..ask import stream_ask
        r0, sp = app.resolve_scope(root)
        app._maybe_reindex(r0, True)       # answers match disk (60s TTL, fail-open)
        stream_ask(r0, a.question, show_map=not a.no_map,
                   docs_only=a.docs, path_filter=sp or None,
                   include_docs=a.with_docs,
                   agents=True if a.agents else (False if a.no_agents else None))
    elif a.cmd == "get":
        from .. import app
        from ..storage.store import resolve_root
        r0, sp = resolve_root(root)
        print(app.get(r0, sp or None, a.file, a.symbol))
    elif a.cmd == "chunks":
        import json as _json

        from .. import app
        from ..storage.store import resolve_root
        r0, sp = resolve_root(root)
        print(_json.dumps(app.chunks(r0, sp or None, a.file, a.query,
                                     path_filter=sp or None), indent=1))
    elif a.cmd == "install":
        from .install import apply, detect, render
        if a.list_only:
            print("Detected AI coding assistants on this machine:")
            for r in detect():
                state = ("registered" if r["registered"] else
                         "not registered" if r["installed"] else "not installed")
                print(f"  {'✓' if r['installed'] else '·'} {r['label']:<12} "
                      f"{state:<16} {r['path']}")
            return
        print(render(apply(platform=a.platform, remove=a.remove), remove=a.remove))
    elif a.cmd in ("serve-api", "serve"):
        from .http import serve
        serve(root, port=a.port, host=a.host, cors=a.cors, enable_llm=not a.no_llm,
              token=a.token, serve_ui=(a.cmd == "serve"))
    elif a.cmd == "forge":
        import json as _json
        if a.specialize:
            from ..forge.specialize import detect_specialization
            opps = detect_specialization(root)
            print(_json.dumps(opps, indent=1) if opps
                  else "no specialization opportunities found")
            if opps:
                print("\n# LLM generation was removed. Write a strategy into "
                      ".megabrain/strategies/ and gate it:\n"
                      "#   from megabrain.forge.specialize import gate_strategy\n"
                      "#   gate_strategy(root, open('strat.py').read(), '.py')")
        elif a.list_only:
            from ..forge import detect
            cands = detect(root)
            print(_json.dumps(cands, indent=1) if cands
                  else "no uncovered text extensions found")
        else:
            from ..forge import forge, render_report
            print(render_report(forge(root, ext=a.ext, dry_run=a.dry_run)))
    elif a.cmd == "flows":
        from ..storage.flows import enabled as _flows_on
        from ..storage.flows import set_enabled
        from ..storage.store import Store
        if a.enable or a.disable:
            set_enabled(root, a.enable)
            print(f"flow cache {'ENABLED' if a.enable else 'disabled'} for {root}")
            return
        if a.warm:
            import json as _json

            from ..ask.warmup import warm_flows
            print(_json.dumps(warm_flows(root, limit=a.warm), indent=1))
            return
        if a.refresh:
            import json as _json

            from ..ask.warmup import refresh_stale
            from ..indexing.indexer import index_repo
            index_repo(root, prune_flows=False)   # update shas, keep flows
            print(_json.dumps(refresh_stale(root), indent=1))
            return
        if not _flows_on(root):
            print("flow cache is OFF for this repo (opt-in). Enable with: "
                  "megabrain flows --enable   ·   or pre-fill: megabrain flows --warm")
            return
        with Store(root) as s:
            metas, _, _ = s.load_flows()
            if a.clear:
                for m in metas:
                    s.delete_flow(m["id"])
                s.commit()
                print(f"cleared {len(metas)} cached flow(s)")
            elif not metas:
                print("no cached flows — they accumulate as you `megabrain ask`")
            else:
                for m in metas:
                    print(f'[{m["id"]}] "{m["question"]}"  '
                          f'({len(m["files"])} files: {", ".join(sorted(m["files"])[:4])}…)')
    elif a.cmd == "trust":
        from ..indexing.strategies import STRATEGY_DIR, trust_file
        sdir = root / STRATEGY_DIR
        files = sorted(sdir.glob("*.py")) if sdir.is_dir() else []
        if not files:
            print(f"nothing to trust: no {STRATEGY_DIR}/*.py in {root}")
        for f in files:
            trust_file(f)
            print(f"trusted {f}")
    elif a.cmd == "stats":
        from .. import app
        st = app.stats(root)
        print(f"files={st['files']} chunks={st['chunks']} symbols={st['symbols']} "
              f"edges={st['edges']} meta={st['last_index']}")
    elif a.cmd == "repos":
        import time as _time

        from ..storage.registry import list_repos
        rows = list_repos()
        if not rows:
            print("no indexed repos registered yet — `megabrain index <path>` adds one")
            return
        for e in rows:
            when = _time.strftime("%Y-%m-%d %H:%M",
                                  _time.localtime(e.get("last_index") or 0))
            print(f'{e["name"]:<22} {e.get("files", 0):>5}f {e.get("chunks", 0):>7}c  '
                  f'{when}  {e.get("embed_model") or "?":<30} {e["path"]}')


if __name__ == "__main__":
    main()
