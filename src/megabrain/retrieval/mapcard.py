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

    # EXPANSION — the query names the SYMPTOM; the mechanism lives under
    # identifiers the query does not contain (jinja lesson: the symptom
    # query missed _textwrap.py entirely). Extract the mechanism identifiers
    # FROM the top matches and pre-run the greps the agent would have run:
    # def site, reader files, incoming edges — deterministic PRF, no LLM.
    qtok = {t.lower() for t in _IDENT.findall(query)}

    def subtoks(name: str) -> set[str]:
        # camelCase / snake_case parts of an identifier, lowered
        parts = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+",
                           name.replace("_", " "))
        return {p.lower() for p in parts if len(p) >= 3}

    # Candidate mechanism identifiers, from the OUTLINE (overlap-ranked per
    # file) NOT the span names — a fat chunk names 4 sibling functions and the
    # span would drag all 4 in (do_filesizeformat/do_pprint alongside
    # do_indent). Rank the candidates by how many sub-tokens they SHARE with
    # the query: do_indent shares "indent", get_usage shares "usage", a chunk
    # neighbour shares nothing and sinks. The symbol the query is about leads;
    # its callees (which may share no token — break_on_hyphens) ride its grep.
    seen_c: set[str] = set()
    scored: list[tuple[int, str]] = []
    for f in ordered[:3]:
        cands = [n.strip() for s in f["spans"]
                 for n in str(s["name"]).split(",")]
        cands += [s["signature"].split("(")[0].split()[-1]
                  for s in f["outline"][:6]]
        for n in cands:
            bare = n.rsplit(".", 1)[-1]
            if (len(bare) < 4 or bare.lower() in qtok or bare in seen_c
                    or not ("_" in bare or not bare.islower() or len(bare) >= 8)):
                continue
            seen_c.add(bare)
            scored.append((len(subtoks(bare) & qtok), bare))
    scored.sort(key=lambda x: -x[0])
    # keep only candidates that actually share a query token; if none do
    # (mechanism named nothing like the symptom), fall back to the top-2 by
    # outline rank so the trail is never empty on a pure-symptom query.
    mech = [b for sc, b in scored if sc > 0] or [b for _, b in scored[:2]]
    trail = []
    if mech:
        from .grepx import grep_repo
        for ident in mech[:4]:
            g = grep_repo(root, ident)
            if not g["matches"]:
                continue
            d = g["defines"][0] if g["defines"] else None
            readers = list(dict.fromkeys(
                m["file"] for m in g["reads"]
                if not d or m["file"] != d["file"]))[:3]
            trail.append({
                "ident": ident,
                "defined": f'{d["file"]}:{d["line"]}' if d else None,
                "readers": readers,
                "reached_from": (d or {}).get("reached_from", [])[:3],
                "tests": list(dict.fromkeys(m["file"] for m in g["tests"]))[:2],
            })
    return {"query": query, "repo": res["repo"], "files": ordered,
            "defines": defines[:4], "trail": trail,
            "pruned": res.get("pruned", 0),
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
    if res.get("trail"):
        L.append("MECHANISM TRAIL (identifiers extracted from the top matches "
                 "— your follow-up greps, pre-run):")
        for t in res["trail"]:
            bits = []
            if t["defined"]:
                bits.append(f'defined {t["defined"]}')
            if t["readers"]:
                bits.append(f'read by {", ".join(t["readers"])}')
            if t["reached_from"]:
                bits.append(f'← {", ".join(t["reached_from"])}')
            if t["tests"]:
                bits.append(f'tests {", ".join(t["tests"])}')
            L.append(f'  {t["ident"]} — {" · ".join(bits)}')
        L.append("")
    if tests:
        L.append("— tests pinning this behavior (the spec — read before changing):")
        for f in tests:
            spans = " · ".join(f'L{s["start_line"]}-{s["end_line"]} {s["name"]}'
                               for s in f["spans"])
            L.append(f'  {f["file"]}  {spans}')
        L.append("")
    return "\n".join(L)
