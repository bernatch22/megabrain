"""Bundle assembly: rank scored chunks and tier them into the retrieval map.

search_with_state() turns score_chunks() output into the view-ready bundle —
Tier 1 (CORE): top files with the full code of their matching chunks + a
symbol index for the rest; Tier 2 (RELATED): remaining candidates + graph
neighbors (+ the flow lane), mapped and expandable next turn. search() /
search_multi() are the one-shot and multi-repo entries; selection() is THE
single definition of what retrieval selected; prune_search() and
chunks_for_file() are pure projections of it.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from .params import DEFAULT_PARAMS
from .scoring import _under_path, score_chunks
from .state import SearchState, load_state

# symbol kinds worth surfacing in the file outline (display only — not ranking).
# Spans Python, TS/JS, Ruby/Go and doc headings so every content type shows.
OUTLINE_KINDS = ("class", "function", "async_function", "method", "async_method",
                 "constant", "const", "var", "interface", "type", "enum",
                 "module", "heading")


def search_with_state(st: SearchState, query: str, rerank: bool = False,
                      path_filter: str | None = None,
                      scored: tuple[list, np.ndarray] | None = None) -> dict:
    """Full retrieval: score every chunk, then rank + tier into CORE/RELATED.
    `scored` accepts a precomputed (metas, fused) from score_chunks so callers
    that also need the raw scores (chunks_for_file) score exactly once."""
    t0 = time.time()
    store, p = st.store, st.params
    metas, fused = scored if scored is not None else score_chunks(st, query, path_filter)
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
    metas, fused = score_chunks(st, query, path_filter)
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
    metas, fused = score_chunks(st, query, path_filter)
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
