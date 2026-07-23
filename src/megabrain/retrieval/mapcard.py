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

import logging
import re
import time
from pathlib import Path

from ..storage.store import Store
from .bundle import prune_search
from .scoring import _is_test_path
from .state import load_state

log = logging.getLogger(__name__)

MAX_FILES = 8
MAX_OUTLINE = 10
MAX_EDGES = 4
MAX_SPANS = 4
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
# path segments marking demo/stub code: shares the subsystem's vocabulary by
# DESIGN while implementing none of it. Field runs: typing-examples/baseline.py
# ranked #2 on the attrs arena and fed the trail NGClass/NGClass2; click's
# examples/ ranked over core. Segment-exact, same stance as TEST_DIR_SEGS.
_DEMO_SEGS = frozenset({"example", "examples", "samples", "demo", "demos",
                        "benchmarks", "typing-examples"})


def _is_demo_path(relpath: str) -> bool:
    return any(p in _DEMO_SEGS for p in relpath.lower().split("/")[:-1])
_OUTLINE_KINDS = ("class", "function", "async_function", "method",
                  "async_method", "interface", "type", "enum", "module")


_EXPAND_MAX_TOKENS = 200
_EXPAND_PROMPT = """A developer is working on this task in a codebase:

{query}

Best matches so far (file · symbols):
{listing}

The task names the SYMPTOM; the mechanism often lives under identifiers the
task never mentions. Name up to 5 code identifiers or short subsystem phrases
likely implementing the mechanism behind this task and MISSING from the list
above. Return ONLY a JSON array of strings, no prose.
Example: ["solve_constraints", "type inference solver"]"""


def _expand_terms(query: str, chunks: list[dict], model: str | None) -> list[str]:
    """One cheap LLM call: mechanism vocabulary the query lacks. The model
    only NAMES search terms — retrieval stays deterministic, so a bad term
    costs one wasted lane, never a wrong span. Raises on any failure; the
    caller fails open."""
    import json as _json

    from .rerank import _hint, judge_lane
    chat, m, _ = judge_lane(model)
    listing = "\n".join(
        f'{c["file"]} · {c.get("name") or c.get("kind") or "?"} · '
        f'{_hint(c, query)}' for c in chunks[:12])
    reply = chat(m, _EXPAND_PROMPT.format(query=query, listing=listing),
                 _EXPAND_MAX_TOKENS, timeout=30)
    arr = re.search(r"\[.*?\]", reply, re.S)
    terms = [str(t).strip() for t in _json.loads(arr.group(0))] if arr else []
    return [t for t in terms if 3 <= len(t) <= 60][:5]


def expand_pool(st, query: str, res: dict, model: str | None = None, *,
                path_filter: str | None = None, with_text: bool = True,
                only_docs: bool = False, exclude_docs: bool = False) -> dict | None:
    """THE EXPANDER, shared by map and search — the judge can only reorder
    what cosine FOUND; when the cause never enters the pool (jinja lesson:
    the symptom query missed _textwrap.py entirely) no reordering rescues
    it. One cheap call names the mechanism vocabulary the query lacks, a
    second deterministic pass over query+terms widens `res["chunks"]` in
    place (deduped by id), and `res["expanded"]` records the terms. The LLM
    never picks spans — it only names search terms. Fails open to None."""
    try:
        terms = _expand_terms(query, res["chunks"], model)
        if not terms:
            return None
        wide = prune_search(st, query + " " + " ".join(terms),
                            path_filter=path_filter, with_text=with_text,
                            only_docs=only_docs, exclude_docs=exclude_docs)
        seen = {c["id"] for c in res["chunks"]}
        res["chunks"] += [c for c in wide["chunks"] if c["id"] not in seen]
        res["kept"] = len(res["chunks"])
        res["expanded"] = {"terms": terms}
        return res["expanded"]
    except Exception:
        log.debug("expansion failed open", exc_info=True)
        return None


def map_repo(root: Path, query: str, path_filter: str | None = None,
             rerank: bool = False, expand: bool = False,
             model: str | None = None) -> dict:
    t0 = time.time()
    root = Path(root)
    # symbol outlines and span lines come from the index — refresh vs disk
    # first (60s TTL, fail-open), same gate as search/ask, or an edited repo
    # maps to yesterday's line numbers.
    from ..indexing.indexer import maybe_reindex
    maybe_reindex(root)
    llm = rerank or expand
    with load_state(root) as st:
        # text is fetched ONLY as evidence for the judge — it never reaches
        # the result or the render (the no-bodies contract is the point).
        res = prune_search(st, query, path_filter=path_filter,
                           with_text=llm, exclude_docs=True)
        expanded = None
        if expand:
            expanded = expand_pool(st, query, res, model,
                                   path_filter=path_filter, with_text=llm,
                                   exclude_docs=True)
    # THE JUDGE — cosine can't tell "formats the symptom" from "causes it":
    # on the mypy field run messages.py (which only BUILDS the error text the
    # query quotes) near-tied with constraints.py (where the fix lived) and
    # won on vocabulary. The rerank judge reorders the pool BEFORE grouping,
    # so file order, the cut, and the trail's top-3 anchor all inherit the
    # verdict. Fail-open: the deterministic order is the floor, never worse.
    # After expansion the judge is also the FILTER that keeps a bad expansion
    # term from polluting the head — judged against the ORIGINAL query.
    judged = None
    if rerank:
        from .rerank import llm_rerank
        # rerank records its drops only into an EXISTING noise list — without
        # this seed the judged-out chunks vanish and the tail can't label them
        # (live run: checkexpr.py dropped invisibly, tail came back empty).
        res.setdefault("noise", [])
        res = llm_rerank(res, query, model=model)
        judged = res.get("reranked") or None
    files: dict[str, dict] = {}
    for pos, c in enumerate(list(res["chunks"]) + list(res.get("tests") or [])):
        f = files.setdefault(c["file"], {"file": c["file"], "score": c["score"],
                                         "pos": pos, "spans": [],
                                         "test": _is_test_path(c["file"])})
        if len(f["spans"]) < MAX_SPANS:
            f["spans"].append({"start_line": c["start_line"],
                               "end_line": c["end_line"],
                               "name": c["name"] or c["kind"]})
    ranked = sorted(files.values(),
                    key=(lambda f: f["pos"]) if judged
                    else (lambda f: -f["score"]))
    # demo/stub files never make the head: they share the subsystem's
    # vocabulary by design (the judge keeps them for exactly that reason)
    # while implementing none of it. They stay on the map — in the tail,
    # labeled — in case the task really is about an example.
    demos = [f for f in ranked if _is_demo_path(f["file"])]
    ranked = [f for f in ranked if not _is_demo_path(f["file"])]
    ordered = ranked[:MAX_FILES]
    # FLAT TAIL — retrieval scores often near-tie past the head (mypy field
    # run: 1.17..1.04 across 13 files) and a hard cut at MAX_FILES throws the
    # cause away exactly when the top is presentation/messaging code that
    # merely NAMES the symptom (messages.py outranked solve.py/constraints.py,
    # where the fix lived). One line per file keeps them on the map.
    tail = [{"file": f["file"], "score": f["score"],
             "span": f'L{f["spans"][0]["start_line"]}-{f["spans"][0]["end_line"]}',
             "names": str(f["spans"][0]["name"])[:80]}
            for f in ranked[MAX_FILES:MAX_FILES + 8] if not f["test"]]
    tail += [{"file": f["file"], "score": f["score"],
              "span": f'L{f["spans"][0]["start_line"]}-{f["spans"][0]["end_line"]}',
              "names": str(f["spans"][0]["name"])[:80], "demo": True}
             for f in demos[:2]]
    # what the judge dropped joins the tail LABELED, never destroyed — judges
    # err (a dropped test file was the spec once, rails#57197), and one line
    # is cheap insurance against a confident wrong exclusion.
    if judged:
        seen_f = {f["file"] for f in ranked}
        for c in (res.get("noise") or [])[:judged["dropped"]]:
            if len(tail) >= 8 or c["file"] in seen_f:
                continue
            seen_f.add(c["file"])
            tail.append({"file": c["file"], "score": c["score"],
                         "span": f'L{c["start_line"]}-{c["end_line"]}',
                         "names": str(c["name"] or c["kind"])[:80],
                         "judged_noise": True})

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
        # SPECIFIC tokens spend the budget first (field run: the agent put
        # do_indent in the query and generic words consumed all 4 slots,
        # pushing the one identifier that mattered out of DEFINES), and a
        # token that is a substring of a more specific one rides it for free.
        defines = []
        toks = sorted(dict.fromkeys(_IDENT.findall(query)),
                      key=lambda t: ("_" in t or not t.islower(), len(t)),
                      reverse=True)
        toks = [t for t in toks
                if not any(t != o and t.lower() in o.lower() for o in toks)]
        for tok in toks:
            if len(defines) >= 4:
                break
            # test files absorb generic names and slip past the ambiguity
            # gate (field runs: "method" -> tests/test_slots.py, "function"/
            # "list" -> mypyc/test-data/fixtures) — a def site inside a test
            # is never the lead, so resolve against non-test symbols only.
            rows = [r for r in store.db.execute(
                "SELECT file, line FROM symbols WHERE name=? "
                "OR name LIKE ? LIMIT 8", (tok, f"%.{tok}")).fetchall()
                if not _is_test_path(r[0])]
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
    # impl files only: the trail is the MECHANISM lane — a test file in the
    # judged top-3 (attrs/click live runs) otherwise floods it with
    # test_* names, which already have two lanes of their own (each trail
    # entry's pre-run grep lists its tests, and the pinning section).
    for f in [f for f in ordered if not f["test"]][:3]:
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
            "tail": tail, "defines": defines[:4], "trail": trail,
            "judged": judged, "expanded": expanded,
            "pruned": res.get("pruned", 0),
            "ms": int((time.time() - t0) * 1000)}


def render_map(res: dict) -> str:
    j = res.get("judged")
    judge = (f' · judged by {j["model"]} (kept {j["kept"]}, dropped {j["dropped"]})'
             if j else "")
    L = [f'# megabrain map — "{res["query"]}"',
         f'repo `{res["repo"]}` · {len(res["files"])} files · {res["ms"]}ms{judge} · '
         f'NO code bodies: batch ALL your Reads in ONE message (each target once), then Edit.\n']
    if res.get("expanded"):
        # the terms double as vocabulary hints for the reader, not just lanes
        L.insert(1, "expanded with mechanism terms: "
                    + ", ".join(res["expanded"]["terms"]))
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
    if res.get("tail"):
        L.append("ALSO MATCHED (scores nearly tie — when the top files only "
                 "FORMAT the symptom, the cause is often down here):")
        for f in res["tail"]:
            mark = (" · judged noise" if f.get("judged_noise")
                    else " · example/stub" if f.get("demo") else "")
            L.append(f'  {f["file"]}  {f["span"]}  {f["names"]}{mark}')
        L.append("")
    if tests:
        L.append("— tests pinning this behavior (the spec — read before changing):")
        for f in tests:
            spans = " · ".join(f'L{s["start_line"]}-{s["end_line"]} {s["name"]}'
                               for s in f["spans"])
            L.append(f'  {f["file"]}  {spans}')
        L.append("")
    return "\n".join(L)
