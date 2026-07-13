"""Query: one-shot retrieval -> view-ready map.

Tier 1 (CORE): top files by fused score — full code of matching chunks +
symbol index for the rest of the file.
Tier 2 (RELATED): remaining candidates + graph neighbors — matched symbols,
docline, line ranges; expandable next turn via `megabrain get`.

No LLM in the path (phase 5: pruning costs completeness; zero added delay).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..providers.embeddings import Embedder
from ..store import Store
from .params import DEFAULT_PARAMS, RetrievalParams

# directory names that mark a file as test/spec code wherever they appear in
# the path. Segment-exact (never substring: "src/contest/" is not a test dir).
# Vocabulary, not a tuning knob — the knobs live in params.RetrievalParams.
TEST_DIR_SEGS = frozenset({"test", "tests", "spec", "specs", "__tests__", "testing"})


def _is_test_path(relpath: str) -> bool:
    """Test-file detector for the ranking down-weight. Two signals:
    any directory segment named test/tests/spec/… (repos use both singular
    and plural), or "test"/"spec" in the FILENAME as a token-ish match
    (foo_test.go, test_foo.py, foo.spec.ts — but not inspect.py/protest.py)."""
    parts = relpath.lower().split("/")
    if any(p in TEST_DIR_SEGS for p in parts[:-1]):
        return True
    return bool(re.search(r"(^|[._-])(test|spec)s?([._-]|$)",
                          parts[-1].rsplit(".", 1)[0]))

# symbol kinds worth surfacing in the file outline (display only — not ranking).
# Spans Python, TS/JS, Ruby/Go and doc headings so every content type shows.
OUTLINE_KINDS = ("class", "function", "async_function", "method", "async_method",
                 "constant", "const", "var", "interface", "type", "enum",
                 "module", "heading")


def _under_path(relpath: str, path_filter: str) -> bool:
    """True when `relpath` is the filter file itself or lives under the filter
    directory (directory-boundary aware, so `src/dispatch` never matches
    `src/dispatcher.ts`). Empty filter matches everything."""
    if not path_filter:
        return True
    pf = path_filter.rstrip("/")
    return relpath == pf or relpath.startswith(pf + "/")


def _apply_path_filter(metas: list, M: np.ndarray, path_filter: str | None):
    """Restrict the candidate chunk set to files under `path_filter`. Applied at
    the START of scoring so the ENTIRE bundle (CORE, RELATED, graph neighbors)
    stays within the sub-path. Fail-open: if the filter matches nothing, return
    the unfiltered set unchanged (a bad/stale subpath won't silently empty the
    bundle). Returns (metas, M) — untouched when no filter."""
    if not path_filter:
        return metas, M
    idx = [i for i, m in enumerate(metas) if _under_path(m.file, path_filter)]
    if not idx:
        return metas, M
    return [metas[i] for i in idx], M[idx]


def _ident_tokens(text: str) -> set[str]:
    """Identifier-aware tokens: split camelCase/snake_case, len>=4 to avoid noise."""
    out = set()
    for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text):
        for p in re.split(r"_+", w):
            for s in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+", p):
                if len(s) >= 4:
                    out.add(s.lower())
    return out


@dataclass
class SearchState:
    """Preloaded, reusable retrieval state for one repo. Build once with
    load_state(); a long-running server (serve.py) keeps it warm so each query
    skips the SQLite matrix load. CLI/MCP go through search(), which builds it
    per call — identical results, just not cached."""
    store: Store
    emb: Embedder
    metas: list
    M: np.ndarray
    fpaths: list
    fskels: list
    F: np.ndarray
    repo: str
    # issue-mode lanes, built lazily on the first long query and cached — a
    # warm server would otherwise rebuild BM25 + the symbol corpus per query.
    bm25: object | None = None
    issue_files: list | None = None
    issue_syms: list | None = None
    # flow cache (flows.py): cached ask syntheses + their matrix, and the last
    # query vector (_score_chunks stashes it so the flow lane re-uses the one
    # embed call — retrieval never embeds twice, never calls an LLM).
    flows: list | None = None
    FL: np.ndarray | None = None
    FLQ: np.ndarray | None = None
    qv: np.ndarray | None = None
    # every tuning knob, injectable (sweeps replace() this instead of
    # monkeypatching module globals). Frozen -> safe to share across threads.
    params: RetrievalParams = DEFAULT_PARAMS

    def close(self) -> None:
        """Release the underlying SQLite connection. One-shot entries
        (search/prune_search_root/…) close via `with`; long-running servers
        close on state reload/shutdown."""
        self.store.close()

    def __enter__(self) -> "SearchState":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def load_state(root: Path, check_same_thread: bool = True,
               params: RetrievalParams | None = None) -> SearchState:
    """Load the per-repo retrieval state (chunk + file matrices) once. The
    expensive part of a query — kept out of the hot path by serve.py.
    `params` injects a tuning variant (default: the validated configuration)."""
    store = Store(Path(root), check_same_thread=check_same_thread)
    metas, M = store.load_matrix()
    fpaths, fskels, F = store.load_file_matrix()
    repo = store.get_meta("repo_name") or Path(root).name
    # flow cache is opt-in and OFF by default: unless the mode is on for this
    # repo, flows stay empty and the read path below is a pure no-op — plain
    # query/ask behave exactly as before, at zero cost.
    from ..flows import enabled as _flows_on
    flows, FL, FLQ = store.load_flows() if _flows_on(root) else ([], None, None)
    return SearchState(store, Embedder(), metas, M, fpaths, fskels, F, repo,
                       flows=flows, FL=FL, FLQ=FLQ,
                       params=params or DEFAULT_PARAMS)


def _score_chunks(st: SearchState, query: str,
                  path_filter: str | None = None) -> tuple[list, np.ndarray]:
    """Full per-chunk scoring (dense + file-fusion + test penalty + issue-mode /
    lexical boosts) WITHOUT ranking/tiering. Returns (path-filtered metas, fused
    score array), index-aligned. Single source of truth for chunk scoring —
    shared by search_with_state() and chunks_for_file()."""
    store, emb, p = st.store, st.emb, st.params
    metas, M = st.metas, st.M
    fpaths, F = st.fpaths, st.F
    if not metas:
        from ..errors import EmptyIndex
        raise EmptyIndex.at()
    # PATH-SCOPE: restrict candidates to files under the sub-path BEFORE scoring,
    # so CORE/RELATED/graph-neighbors all stay within it. No filter -> unchanged.
    metas, M = _apply_path_filter(metas, M, path_filter)
    qv = emb.embed([query])[0]
    st.qv = qv                     # re-used by the flow lane (no second embed)

    dense = (M @ qv + 1) / 2
    fscore = (F @ qv + 1) / 2
    f2i = {f: i for i, f in enumerate(fpaths)}
    cfi = np.array([f2i.get(m.file, -1) for m in metas])
    fused = dense + p.file_fusion_w * np.where(cfi >= 0, fscore[cfi], 0.5)
    # soft down-weight for test files: keep them reachable, stop them crowding
    is_test = np.array([_is_test_path(m.file) for m in metas])
    fused = np.where(is_test, fused * p.test_penalty, fused)

    # issue mode (long queries, e.g. bug reports): deterministic grounding —
    # traceback frames pin files/spans, identifiers boost, tests fully masked
    # (LocAgent-style; gold files for issues are never tests).
    qtok = _ident_tokens(query)
    if len(qtok) > p.issue_token_threshold:
        from .bm25 import BM25
        from .bm25 import tokenize as _bt
        from .issue import parse_issue, query_variants
        # sparse entity-ID lane (LocAgent T4), built only in issue mode:
        # BM25 over each file's path + symbol names + signatures. Built over
        # the UNfiltered file set, so it caches safely across path filters.
        if st.bm25 is None:
            file_docs = []
            for f in fpaths:
                toks = re.findall(r"[A-Za-z0-9_]+", f)
                for s in store.symbols_for(f):
                    toks.append(s["name"])
                    if s.get("signature"):
                        toks.append(s["signature"])
                file_docs.append(_bt(" ".join(toks)))
            st.bm25 = BM25(file_docs)
        bm25_fscore = st.bm25.scores(query)
        bf2i = {f: i for i, f in enumerate(fpaths)}
        cbi = np.array([bf2i.get(m.file, -1) for m in metas])
        bm25_chunk = np.where(cbi >= 0, bm25_fscore[cbi], 0.0)
        # variant ensemble: title/traceback/code/identifier views, ONE batch
        # embed call (no extra latency), RRF-merged into the dense lane
        variants = query_variants(query)
        if variants:
            VV = emb.embed(variants)
            rankings = [fused]
            for v in range(len(variants)):
                dv = (M @ VV[v] + 1) / 2
                fv = (F @ VV[v] + 1) / 2
                rankings.append(dv + p.file_fusion_w * np.where(cfi >= 0, fv[cfi], 0.5))
            rankings.append(bm25_chunk)  # sparse entity-ID lane
            rrf = np.zeros(len(metas))
            for s in rankings:
                order_ = np.argsort(-s)
                ranks = np.empty(len(metas), dtype=int)
                ranks[order_] = np.arange(len(metas))
                rrf += 1.0 / (p.rrf_k + ranks + 1)
            # full-issue ranking keeps double weight
            order_ = np.argsort(-fused)
            ranks = np.empty(len(metas), dtype=int)
            ranks[order_] = np.arange(len(metas))
            rrf += 1.0 / (p.rrf_k + ranks + 1)
            fused = rrf / rrf.max() if rrf.max() > 0 else rrf  # [0,1] so tier bonuses keep scale
        # symbol corpus for grounding: cache only the unfiltered view (under a
        # path filter, grounding must stay within the filtered file set).
        if path_filter is None and st.issue_syms is not None:
            all_files, all_syms = st.issue_files, st.issue_syms
        else:
            all_files = list(dict.fromkeys(m.file for m in metas))
            all_syms = []
            for f in all_files:
                for s in store.symbols_for(f):
                    all_syms.append({"file": f, "name": s["name"],
                                     "line": s["line"], "end_line": s["end_line"]})
            if path_filter is None:
                st.issue_files, st.issue_syms = all_files, all_syms
        g = parse_issue(query, all_files, all_syms)
        fused = np.where(is_test, -1.0, fused)
        span_by_file: dict[str, list[tuple[int, int]]] = {}
        for f, lo, hi in g["pin_spans"]:
            span_by_file.setdefault(f, []).append((lo, hi))
        for i, m in enumerate(metas):
            tier = g["pin_files"].get(m.file)
            if tier is None:
                continue
            fused[i] += p.tier_bonus[tier]
            for lo, hi in span_by_file.get(m.file, []):
                if not (m.end_line < lo or m.start_line > hi):
                    fused[i] += p.span_bonus
                    break

    # exact symbol/filename token match -> additive boost (lexical lane).
    # Only for short developer queries: long texts (issue reports) carry so many
    # identifier tokens that the boost becomes uniform noise.
    if qtok and len(qtok) <= p.issue_token_threshold:
        boost = np.zeros(len(metas))
        for i, m in enumerate(metas):
            stem = m.file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            nf = len(_ident_tokens(stem) & qtok)
            ns = len(_ident_tokens(m.name or "") & qtok)
            boost[i] = max(p.file_boost_w * min(nf, p.lexical_boost_cap),
                           p.sym_boost_w * min(ns, p.lexical_boost_cap))
        fused = fused + boost
        # NB: BM25 sparse lane is deliberately NOT blended into short dev queries
        # — it raised SWE recall but cost golden bundle completeness (the product
        # priority). It stays in issue-mode RRF only, where rerank cleans ordering.

    return metas, fused


def search_with_state(st: SearchState, query: str, rerank: bool = False,
                      path_filter: str | None = None,
                      scored: tuple[list, np.ndarray] | None = None) -> dict:
    """Full retrieval: score every chunk, then rank + tier into CORE/RELATED.
    `scored` accepts a precomputed (metas, fused) from _score_chunks so callers
    that also need the raw scores (chunks_for_file) score exactly once."""
    t0 = time.time()
    store, p = st.store, st.params
    metas, fused = scored if scored is not None else _score_chunks(st, query, path_filter)
    order = np.argsort(-fused)
    file_rank: list[str] = []
    file_chunks: dict[str, list[int]] = {}
    for ci in order:
        f = metas[ci].file
        if f not in file_rank:
            file_rank.append(f)
        file_chunks.setdefault(f, []).append(int(ci))
    cands = file_rank[:p.cand_files]

    neigh: set[str] = set()
    for f in cands[:3]:
        neigh |= store.neighbors(f)
    neigh -= set(cands)
    fbest = {f: fused[file_chunks[f][0]] for f in file_rank}
    extras = sorted(neigh & set(file_rank), key=lambda f: -fbest[f])[:p.graph_extras]

    if rerank and len(cands) > 1:
        # optional LLM ORDER rerank: code evidence + 3-vote merge (+~2-3s)
        from .rerank import llm_order
        # deeper pool when reranking: the LLM can rescue rank 13-24
        deep = file_rank[:p.rerank_deep_pool]
        for f in deep:
            if f not in cands:
                cands.append(f)
        ev = [{"file": f, "code": metas[file_chunks[f][0]].text} for f in cands]
        order = llm_order(query, ev)
        cands = [cands[i] for i in order]

    tier1 = cands[:p.tier1_max]
    # adaptive CORE: only files within tier1_gap of the top get full code;
    # the rest demote to the map (bundle membership unchanged)
    top_score = fbest[tier1[0]]
    tier1 = [f for f in tier1 if fbest[f] >= top_score * p.tier1_gap] or tier1[:1]
    tier2 = [f for f in cands if f not in tier1] + extras

    out_t1 = []
    for f in tier1:
        idxs = file_chunks[f]
        best = fused[idxs[0]]
        keep = [i for i in idxs
                if fused[i] >= best * p.chunk_keep_ratio][:p.tier1_chunk_cap] or idxs[:1]
        keep.sort(key=lambda i: metas[i].start_line)
        out_t1.append({
            "file": f, "score": float(best),
            "chunks": [{**metas[i].to_dict(), "score": float(fused[i])} for i in keep],
            "symbols": store.symbols_for(f),
            "neighbors": sorted(store.neighbors(f) & set(cands + extras)),
        })
    out_t2 = []
    for f in tier2:
        idxs = file_chunks.get(f, [])
        matched = [metas[i].name for i in idxs[:3] if metas[i].name]
        syms = store.symbols_for(f)
        docline = next((s["doc"] for s in syms if s["doc"]), None)
        best_chunk = metas[idxs[0]].to_dict() if idxs else None
        out_t2.append({
            "file": f, "score": float(fbest.get(f, 0)),
            "via_graph": f in extras, "matched": matched, "doc": docline,
            "best_chunk": best_chunk,
            "symbols": [s for s in syms if s["kind"] in OUTLINE_KINDS][:12],
        })
    # FLOW LANE (flows.py): cached ask syntheses matching this query — cosine
    # only against the already-computed query vector. Flows ATTACH; they never
    # rank or displace files. Their source files append to the RELATED tail
    # only when missing entirely — pure additions, bundle_full can only rise.
    flows_out = []
    if st.flows and st.qv is not None:
        from ..flows import FLOW_FILE_ADDS, match_flows
        flows_out = match_flows(st.flows, st.FL, st.qv, st.FLQ)
        have = {t["file"] for t in out_t1} | {t["file"] for t in out_t2}
        adds = 0
        for fl in flows_out:
            for f in fl["files"]:
                if f in have or adds >= FLOW_FILE_ADDS or not _under_path(f, path_filter or ""):
                    continue
                syms = store.symbols_for(f)
                out_t2.append({
                    "file": f, "score": float(fbest.get(f, 0)),
                    "via_graph": False, "via_flow": True,
                    "matched": [], "doc": next((s["doc"] for s in syms if s["doc"]), None),
                    "best_chunk": (metas[file_chunks[f][0]].to_dict()
                                   if file_chunks.get(f) else None),
                    "symbols": [s for s in syms if s["kind"] in OUTLINE_KINDS][:12],
                })
                have.add(f)
                adds += 1

    return {"query": query, "tier1": out_t1, "tier2": out_t2,
            "flows": flows_out,
            "repo": st.repo,
            "ms": int((time.time() - t0) * 1000)}


def search(root: Path, query: str, rerank: bool = False,
           path_filter: str | None = None) -> dict:
    """One-shot retrieval (CLI/MCP entry). Builds state then queries — identical
    output to search_with_state(load_state(root), ...). `path_filter` (a POSIX
    subpath relative to root) scopes retrieval to files under it (PATH-SCOPE)."""
    with load_state(Path(root)) as st:
        return search_with_state(st, query, rerank, path_filter)


def selection(res: dict) -> list[tuple[dict, float]]:
    """THE single definition of what retrieval SELECTED out of a bundle: every
    tier-1 chunk that survived the chunk_keep_ratio cut, plus each RELATED
    file's best chunk — (chunk dict, relevance score), tier1 first, deduped.
    prune_search and chunks_for_file are both projections of this; keep the
    semantics here and nowhere else."""
    out: list[tuple[dict, float]] = []
    seen: set = set()
    for t in res["tier1"]:
        for c in t["chunks"]:
            if c["id"] not in seen:
                seen.add(c["id"])
                out.append((c, float(c["score"])))
    for t in res["tier2"]:
        bc = t.get("best_chunk")
        if bc and bc["id"] not in seen:
            seen.add(bc["id"])
            out.append((bc, float(t["score"])))
    return out


def chunks_for_file(st: SearchState, relpath: str, query: str,
                    path_filter: str | None = None) -> dict:
    """One file + query → EVERY chunk of that file with its span, relevance
    score, and whether the full retrieval SELECTED it into the bundle
    (selection() — what the agent would actually read, not an intra-file
    threshold). Powers the chunk-selection demo UI."""
    metas, fused = _score_chunks(st, query, path_filter)
    res = search_with_state(st, query, path_filter=path_filter,
                            scored=(metas, fused))
    selected = {c["id"] for c, _ in selection(res)}
    role = "unranked"
    if any(t["file"] == relpath for t in res["tier1"]):
        role = "core"
    elif any(t["file"] == relpath for t in res["tier2"]):
        role = "related"
    rows = []
    for i, m in enumerate(metas):
        if m.file != relpath:
            continue
        rows.append({
            "id": m.id, "kind": m.kind, "name": m.name, "part": m.part,
            "start_line": m.start_line, "end_line": m.end_line,
            "breadcrumb": m.breadcrumb, "text": m.text,
            "score": float(fused[i]), "selected": m.id in selected,
        })
    rows.sort(key=lambda r: r["start_line"])
    scores = [r["score"] for r in rows] or [0.0]
    return {"file": relpath, "query": query, "role": role, "repo": st.repo,
            "score_min": float(min(scores)), "score_max": float(max(scores)),
            "selected_count": sum(1 for r in rows if r["selected"]),
            "chunks": rows}


def chunks_for_file_root(root: Path, relpath: str, query: str,
                         path_filter: str | None = None) -> dict:
    """CLI/one-shot entry for chunks_for_file (builds state then queries)."""
    with load_state(Path(root)) as st:
        return chunks_for_file(st, relpath, query, path_filter)


def prune_search(st: SearchState, query: str, path_filter: str | None = None,
                 with_text: bool = True, include_pruned: bool = False) -> dict:
    """NO-LLM noise pruning. Runs the full retrieval, then returns ONLY the
    SELECTED (signal) chunks as a FLAT list ordered by relevance — the exact
    chunk ids/spans an agent should read, with the rest (noise) dropped. Same
    selection the demo's signal/noise map uses: a tier-1 chunk that survives the
    CHUNK_KEEP_RATIO cut, or a related file's best chunk. Deterministic and
    cheap — the lean alternative to `ask` when the caller just needs the right
    code, not a narration (a modern LLM needs no pre-filtered prose).

    `include_pruned` also returns the dropped chunks (the bundle files' non-signal
    chunks, relevance-ordered) under "noise" — for a signal-vs-noise diff view."""
    metas, fused = _score_chunks(st, query, path_filter)
    res = search_with_state(st, query, path_filter=path_filter, scored=(metas, fused))

    def rec(c: dict, score: float) -> dict:
        item = {"id": c["id"], "file": c["file"],
                "start_line": c["start_line"], "end_line": c["end_line"],
                "kind": c["kind"], "name": c["name"], "score": round(float(score), 4)}
        if with_text:
            item["text"] = c["text"]
        return item

    # a pure projection of selection() — the ONE definition of signal
    picked = selection(res)
    seen = {c["id"] for c, _ in picked}
    kept = sorted((rec(c, s) for c, s in picked), key=lambda c: -c["score"])
    # honest noise count: chunks living in the bundle's files that we dropped.
    bundle_files = {t["file"] for t in res["tier1"]} | {t["file"] for t in res["tier2"]}
    noise: list[dict] = []
    in_bundle = 0
    for i, m in enumerate(metas):
        if m.file not in bundle_files:
            continue
        in_bundle += 1
        if include_pruned and m.id not in seen:
            noise.append(rec(m.to_dict(), fused[i]))
    noise.sort(key=lambda c: -c["score"])
    out = {"query": query, "repo": st.repo, "chunks": kept,
           "kept": len(kept), "pruned": max(0, in_bundle - len(kept)),
           "scanned": in_bundle, "ms": res["ms"]}
    if include_pruned:
        out["noise"] = noise
    return out


def prune_search_root(root: Path, query: str, path_filter: str | None = None,
                      with_text: bool = True, include_pruned: bool = False) -> dict:
    """CLI/MCP one-shot entry for prune_search (builds state then queries)."""
    with load_state(Path(root)) as st:
        return prune_search(st, query, path_filter, with_text, include_pruned)


def render_pruned(res: dict, with_text: bool = True) -> str:
    """Pruned result -> ranked markdown list: `[id] file L… (name) · score`,
    each with its code (unless with_text=False). Noise dropped, signal only."""
    L: list[str] = []
    L.append(f'# megabrain prune — "{res["query"]}"')
    L.append(f'repo `{res["repo"]}` · {res["kept"]} signal chunks '
             f'({res["pruned"]} pruned as noise) · {res["ms"]}ms\n')
    for rank, c in enumerate(res["chunks"], 1):
        label = c["name"] or c["kind"]
        L.append(f'### {rank}. [{c["id"]}] {c["file"]} '
                 f'L{c["start_line"]}-{c["end_line"]} · {label} · `{c["score"]:.3f}`')
        if with_text and c.get("text"):
            L.append(f'```{lang_of(c["file"])}')
            L.append(c["text"].rstrip("\n"))
            L.append("```")
        L.append("")
    return "\n".join(L)


def search_multi(roots: list[Path], query: str,
                 path_filters: list[str | None] | None = None) -> dict:
    """Search several repos, merge by score (same embedder -> comparable).
    Files are prefixed repo-name/path. Tier1 capped at TIER1_MAX+2 across repos.
    `path_filters` (one per root, or None) applies PATH-SCOPE per repo."""
    t0 = time.time()
    pfs = path_filters or [None] * len(roots)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(len(roots), 8)) as ex:
        results = list(ex.map(lambda rp: search(rp[0], query, path_filter=rp[1]),
                              zip(roots, pfs)))
    if len(results) == 1:
        return results[0]
    t1, t2 = [], []
    for res in results:
        for t in res["tier1"]:
            t1.append({**t, "file": f'{res["repo"]}/{t["file"]}', "_repo": res["repo"]})
        for t in res["tier2"]:
            t2.append({**t, "file": f'{res["repo"]}/{t["file"]}', "_repo": res["repo"]})
    t1.sort(key=lambda t: -t["score"])
    t2.sort(key=lambda t: -t["score"])
    cap = DEFAULT_PARAMS.tier1_max + DEFAULT_PARAMS.multi_tier1_extra
    promoted = t1[:cap]
    demoted = [{"file": t["file"], "score": t["score"], "via_graph": False,
                "matched": [c["name"] for c in t["chunks"][:3] if c["name"]],
                "doc": None,
                "symbols": [s for s in t["symbols"]
                            if s["kind"] in OUTLINE_KINDS][:12]}
               for t in t1[cap:]]
    return {"query": query, "tier1": promoted, "tier2": demoted + t2,
            "repo": "+".join(r["repo"] for r in results),
            "ms": int((time.time() - t0) * 1000)}


# ---------------------------------------------------------------- rendering


def lang_of(path: str) -> str:
    return {"py": "python", "ts": "typescript", "tsx": "tsx", "js": "javascript",
            "jsx": "jsx", "mjs": "javascript", "cjs": "javascript", "rb": "ruby",
            "go": "go", "php": "php", "md": "markdown", "markdown": "markdown",
            "mdx": "markdown"}.get(path.rsplit(".", 1)[-1], "")


def render(res: dict, compact: bool = False, related_code: bool = False) -> str:
    """Bundle dict -> view-ready markdown map.

    RELATED renders as a MAP by default — file, best-match span pointer,
    symbols — without chunk code bodies. Measured on the golden set, RELATED
    holds 45% of the gold files (it cannot be dropped) but ~95% of its VOLUME
    is non-gold code bodies that flooded agent context windows (~16K of a
    ~22K-token bundle). The bundle DATA is unchanged (ask/serve consume
    best_chunk as before); `related_code=True` (CLI/MCP: full) restores the
    old inline-code render."""
    L: list[str] = []
    n1, n2 = len(res["tier1"]), len(res["tier2"])
    L.append(f'# megabrain — "{res["query"]}"')
    L.append(f'repo `{res["repo"]}` · {n1} core files (full code) · {n2} related (mapped) · {res["ms"]}ms\n')

    # cached flows first: a previously-synthesized walkthrough of this very
    # workflow is the highest-density context in the bundle. Clearly labeled as
    # a cached synthesis — the code truth stays in CORE/RELATED below.
    for fl in res.get("flows", []):
        L.append(f'## KNOWN FLOW (cached ask) — "{fl["question"]}"  `{fl["score"]:.2f}`')
        L.append(f'sources: {", ".join(fl["files"])}\n')
        if not compact:
            L.append(fl["text"].rstrip())
        L.append("")

    L.append("## CORE\n")
    for i, t in enumerate(res["tier1"], 1):
        L.append(f'### {i}. {t["file"]}  `{t["score"]:.2f}`')
        if t["neighbors"]:
            L.append(f'linked: {", ".join(t["neighbors"])}')
        covered = []
        for c in t["chunks"]:
            covered.append((c["start_line"], c["end_line"]))
            part = f' (part {c["part"]})' if c["part"] else ""
            L.append(f'\n**{c["name"] or c["kind"]}** L{c["start_line"]}-{c["end_line"]}{part}')
            if not compact:
                L.append(f'```{lang_of(t["file"])}')
                L.append(c["text"].rstrip("\n"))
                L.append("```")
        rest = [s for s in t["symbols"]
                if not any(lo <= s["line"] <= hi for lo, hi in covered)
                and s["kind"] in OUTLINE_KINDS]
        if rest:
            L.append("\nrest of file:")
            for s in rest[:20]:
                d = f' — {s["doc"]}' if s["doc"] else ""
                L.append(f'- `{s["signature"]}` L{s["line"]}{d}')
        L.append("")

    if res["tier2"]:
        hint = "" if related_code else " · code bodies: `--full`"
        L.append("## RELATED — best match + symbols per file · expand with "
                 f"`megabrain get <file> [--symbol NAME]`{hint}\n")
        for t in res["tier2"]:
            via = " ·via-graph" if t["via_graph"] else (
                " ·via-flow" if t.get("via_flow") else "")
            match = f' · matched: {", ".join(t["matched"])}' if t["matched"] else ""
            doc = f' — {t["doc"]}' if t["doc"] else ""
            L.append(f'### {t["file"]}  `{t["score"]:.2f}`{via}{match}{doc}')
            bc = t.get("best_chunk")
            if bc and not compact:
                L.append(f'**{bc["name"] or bc["kind"]}** L{bc["start_line"]}-{bc["end_line"]}')
                if related_code:
                    L.append(f'```{lang_of(t["file"])}')
                    L.append(bc["text"].rstrip("\n"))
                    L.append("```")
            for s in t["symbols"][:6]:
                L.append(f'- `{s["signature"]}` L{s["line"]}-{s["end_line"]}')
            L.append("")
    return "\n".join(L)


def get_code(root: Path, relpath: str, symbol: str | None = None) -> str:
    root = Path(root).resolve()
    p = (root / relpath).resolve()
    # containment check: `relpath` is attacker-adjacent when served over HTTP
    # (serve.py /get) or MCP — `../../etc/passwd` must never escape the repo.
    if not p.is_relative_to(root) or not p.exists():
        return f"not found: {relpath}"
    src = p.read_text(errors="replace")
    if not symbol:
        return f"```{lang_of(relpath)}\n{src}\n```"
    with Store(Path(root)) as store:
        syms = [s for s in store.symbols_for(relpath)
                if s["name"] == symbol or s["name"].endswith("." + symbol)]
    if not syms:
        return f"symbol {symbol} not found in {relpath}"
    lines = src.splitlines(keepends=True)
    out = []
    for s in syms:
        code = "".join(lines[s["line"] - 1:s["end_line"]])
        out.append(f'# {relpath} > {s["signature"]} (L{s["line"]}-{s["end_line"]})\n'
                   f'```{lang_of(relpath)}\n{code}```')
    return "\n\n".join(out)
