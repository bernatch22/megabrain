"""megabrain map — the task-level structure card: where, who, what shape.

NO code bodies, ever. The duel accounting proved bodies from the MCP get paid
twice on implement tasks (the host requires Read before Edit), while the
winning workflow the agents converged on under a token budget was
index -> ONE Read per edit target -> Edit. This tool IS that index, in one
call: the semantic lane (scored files + match-span pointers), the AST-level
symbol outline (signatures with line ranges, from the symbols table), the
import/call edges BOTH ways (who reaches this file, what it reaches), the
literal lane (exact identifiers from the query resolved to their def sites),
and the tests that pin the behavior. Deterministic, no LLM — grep-priced,
map-shaped.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from ..storage.store import Store
from .bundle import prune_search
from .scoring import _is_test_path
from .state import load_state

MAX_FILES = 8
MAX_OUTLINE = 10
MAX_EDGES = 4
MAX_SPANS = 4
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
_OUTLINE_KINDS = ("class", "function", "async_function", "method",
                  "async_method", "interface", "type", "enum", "module")


def map_repo(root: Path, query: str, path_filter: str | None = None) -> dict:
    t0 = time.time()
    root = Path(root)
    with load_state(root) as st:
        res = prune_search(st, query, path_filter=path_filter,
                           with_text=False, exclude_docs=True)
    files: dict[str, dict] = {}
    for c in res["chunks"]:
        f = files.setdefault(c["file"], {"file": c["file"], "score": c["score"],
                                         "spans": [], "test": _is_test_path(c["file"])})
        if len(f["spans"]) < MAX_SPANS:
            f["spans"].append({"start_line": c["start_line"],
                               "end_line": c["end_line"],
                               "name": c["name"] or c["kind"]})
    ordered = sorted(files.values(), key=lambda f: -f["score"])[:MAX_FILES]

    with Store(root) as store:
        for rank, f in enumerate(ordered, 1):
            spans = [(s["start_line"], s["end_line"]) for s in f["spans"]]

            def overlap(s, spans=tuple(spans)):
                lo, hi = s["line"], s.get("end_line") or s["line"]
                return sum(min(hi, b) - max(lo, a) + 1
                           for a, b in spans if not (hi < a or lo > b))
            syms = [s for s in store.symbols_for(f["file"])
                    if s["kind"] in _OUTLINE_KINDS]
            # RELEVANT outline, not the file's first N: symbols overlapping
            # the match spans first (the live version listed do_upper/do_lower
            # for a do_indent question — file order is noise order). Full
            # outline only for the top files; the tail keeps spans + edges.
            syms.sort(key=lambda s: (-overlap(s), s["line"]))
            cap = MAX_OUTLINE if rank <= 3 else 0
            f["outline"] = [
                {"signature": s["signature"][:88], "line": s["line"],
                 "end_line": s.get("end_line") or s["line"],
                 "doc": (s.get("doc") or "")[:60] or None}
                for s in syms[:cap]]
            f["reached_from"] = [r[0] for r in store.db.execute(
                "SELECT DISTINCT src FROM edges WHERE dst=? LIMIT ?",
                (f["file"], MAX_EDGES))]
            f["reaches"] = [r[0] for r in store.db.execute(
                "SELECT DISTINCT dst FROM edges WHERE src=? LIMIT ?",
                (f["file"], MAX_EDGES))]
        # literal lane: exact identifiers from the query -> def sites.
        # A token with MANY def sites is a generic word ("filter", "first"),
        # not a lead — ambiguity is noise, so it is dropped, not listed.
        defines = []
        for tok in dict.fromkeys(_IDENT.findall(query)):
            rows = store.db.execute(
                "SELECT file, line FROM symbols WHERE name=? "
                "OR name LIKE ? LIMIT 4", (tok, f"%.{tok}")).fetchall()
            if 1 <= len(rows) <= 2:
                defines += [{"token": tok, "file": fl, "line": ln}
                            for fl, ln in rows]
    return {"query": query, "repo": res["repo"], "files": ordered,
            "defines": defines[:4], "pruned": res.get("pruned", 0),
            "ms": int((time.time() - t0) * 1000)}


def render_map(res: dict) -> str:
    L = [f'# megabrain map — "{res["query"]}"',
         f'repo `{res["repo"]}` · {len(res["files"])} files · {res["ms"]}ms · '
         f'NO code bodies: Read an edit target ONCE, then Edit.\n']
    if res["defines"]:
        L.append("DEFINES (exact identifiers from your query):")
        for d in res["defines"]:
            L.append(f'  {d["token"]} -> {d["file"]}:{d["line"]}')
        L.append("")
    impl = [f for f in res["files"] if not f["test"]]
    tests = [f for f in res["files"] if f["test"]]
    for f in impl:
        L.append(f'## {f["file"]}  `{f["score"]:.2f}`')
        spans = " · ".join(f'L{s["start_line"]}-{s["end_line"]} {s["name"][:48]}'
                           for s in f["spans"][:3])
        L.append(f'   match: {spans}')
        for s in f["outline"]:
            doc = f' — {s["doc"]}' if s.get("doc") else ""
            L.append(f'   {s["signature"]}  L{s["line"]}-{s["end_line"]}{doc}')
        if f["reached_from"]:
            L.append(f'   ← reached from: {", ".join(f["reached_from"])}')
        if f["reaches"]:
            L.append(f'   → reaches: {", ".join(f["reaches"])}')
        L.append("")
    if tests:
        L.append("— tests pinning this behavior (the spec — read before changing):")
        for f in tests:
            spans = " · ".join(f'L{s["start_line"]}-{s["end_line"]} {s["name"]}'
                               for s in f["spans"])
            L.append(f'  {f["file"]}  {spans}')
        L.append("")
    return "\n".join(L)
