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

from .embeddings import Embedder
from .store import Store

FILE_FUSION_W = 0.5     # phase 3 winner
TIER1_MAX = 4
TIER1_GAP = 0.97        # full code only for files within 3% of top score (noise control)
CAND_FILES = 12
GRAPH_EXTRAS = 6        # graph neighbors of top files pulled into tier2 (recall-safe:
                        # never touches tier1/R@1; more candidates only lift bundle_full)
CHUNK_KEEP_RATIO = 0.8  # within a tier-1 file, keep chunks >= ratio * best chunk
TEST_PENALTY = 0.85     # soft down-weight for test files in ranking
FILE_BOOST_W = 0.05     # per matched filename token (capped at 2; grid-tuned p6)
SYM_BOOST_W = 0.03      # per matched symbol-name token (capped at 2; grid-tuned p6)

# symbol kinds worth surfacing in the file outline (display only — not ranking).
# Spans Python, TS/JS, Ruby/Go and doc headings so every content type shows.
OUTLINE_KINDS = ("class", "function", "async_function", "method", "async_method",
                 "constant", "const", "var", "interface", "type", "enum",
                 "module", "heading")


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


def load_state(root: Path, check_same_thread: bool = True) -> SearchState:
    """Load the per-repo retrieval state (chunk + file matrices) once. The
    expensive part of a query — kept out of the hot path by serve.py."""
    store = Store(Path(root), check_same_thread=check_same_thread)
    metas, M = store.load_matrix()
    fpaths, fskels, F = store.load_file_matrix()
    repo = store.get_meta("repo_name") or Path(root).name
    return SearchState(store, Embedder(), metas, M, fpaths, fskels, F, repo)


def search_with_state(st: SearchState, query: str, rerank: bool = False) -> dict:
    t0 = time.time()
    store, emb = st.store, st.emb
    metas, M = st.metas, st.M
    fpaths, fskels, F = st.fpaths, st.fskels, st.F
    if not metas:
        raise RuntimeError("index is empty — run: megabrain index")
    qv = emb.embed([query])[0]

    dense = (M @ qv + 1) / 2
    fscore = (F @ qv + 1) / 2
    f2i = {f: i for i, f in enumerate(fpaths)}
    cfi = np.array([f2i.get(m["file"], -1) for m in metas])
    fused = dense + FILE_FUSION_W * np.where(cfi >= 0, fscore[cfi], 0.5)
    # soft down-weight for test files: keep them reachable, stop them crowding
    is_test = np.array([("test" in m["file"].split("/")[0:2][-1].lower()
                         or "/tests/" in m["file"] or m["file"].startswith("tests/"))
                        for m in metas])
    fused = np.where(is_test, fused * TEST_PENALTY, fused)

    # issue mode (long queries, e.g. bug reports): deterministic grounding —
    # traceback frames pin files/spans, identifiers boost, tests fully masked
    # (LocAgent-style; gold files for issues are never tests).
    qtok = _ident_tokens(query)
    if len(qtok) > 25:
        from .bm25 import BM25
        from .bm25 import tokenize as _bt
        from .issue import parse_issue, query_variants
        # sparse entity-ID lane (LocAgent T4), built only in issue mode:
        # BM25 over each file's path + symbol names + signatures.
        file_docs = []
        for f in fpaths:
            toks = re.findall(r"[A-Za-z0-9_]+", f)
            for s in store.symbols_for(f):
                toks.append(s["name"])
                if s.get("signature"):
                    toks.append(s["signature"])
            file_docs.append(_bt(" ".join(toks)))
        bm25_fscore = BM25(file_docs).scores(query)
        bf2i = {f: i for i, f in enumerate(fpaths)}
        cbi = np.array([bf2i.get(m["file"], -1) for m in metas])
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
                rankings.append(dv + FILE_FUSION_W * np.where(cfi >= 0, fv[cfi], 0.5))
            rankings.append(bm25_chunk)  # sparse entity-ID lane
            rrf = np.zeros(len(metas))
            for s in rankings:
                order_ = np.argsort(-s)
                ranks = np.empty(len(metas), dtype=int)
                ranks[order_] = np.arange(len(metas))
                rrf += 1.0 / (60 + ranks + 1)
            # full-issue ranking keeps double weight
            order_ = np.argsort(-fused)
            ranks = np.empty(len(metas), dtype=int)
            ranks[order_] = np.arange(len(metas))
            rrf += 1.0 / (60 + ranks + 1)
            fused = rrf / rrf.max() if rrf.max() > 0 else rrf  # [0,1] so tier bonuses keep scale
        all_files = list(dict.fromkeys(m["file"] for m in metas))
        all_syms = []
        for f in all_files:
            for s in store.symbols_for(f):
                all_syms.append({"file": f, "name": s["name"],
                                 "line": s["line"], "end_line": s["end_line"]})
        g = parse_issue(query, all_files, all_syms)
        fused = np.where(is_test, -1.0, fused)
        TIER_BONUS = {0: 0.6, 1: 0.25, 2: 0.10}
        span_by_file: dict[str, list[tuple[int, int]]] = {}
        for f, lo, hi in g["pin_spans"]:
            span_by_file.setdefault(f, []).append((lo, hi))
        for i, m in enumerate(metas):
            tier = g["pin_files"].get(m["file"])
            if tier is None:
                continue
            fused[i] += TIER_BONUS[tier]
            for lo, hi in span_by_file.get(m["file"], []):
                if not (m["end_line"] < lo or m["start_line"] > hi):
                    fused[i] += 0.15
                    break

    # exact symbol/filename token match -> additive boost (lexical lane).
    # Only for short developer queries: long texts (issue reports) carry so many
    # identifier tokens that the boost becomes uniform noise.
    if qtok and len(qtok) <= 25:
        boost = np.zeros(len(metas))
        for i, m in enumerate(metas):
            stem = m["file"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
            nf = len(_ident_tokens(stem) & qtok)
            ns = len(_ident_tokens(m["name"] or "") & qtok)
            boost[i] = max(FILE_BOOST_W * min(nf, 2), SYM_BOOST_W * min(ns, 2))
        fused = fused + boost
        # NB: BM25 sparse lane is deliberately NOT blended into short dev queries
        # — it raised SWE recall but cost golden bundle completeness (the product
        # priority). It stays in issue-mode RRF only, where rerank cleans ordering.

    order = np.argsort(-fused)
    file_rank: list[str] = []
    file_chunks: dict[str, list[int]] = {}
    for ci in order:
        f = metas[ci]["file"]
        if f not in file_rank:
            file_rank.append(f)
        file_chunks.setdefault(f, []).append(int(ci))
    cands = file_rank[:CAND_FILES]

    neigh: set[str] = set()
    for f in cands[:3]:
        neigh |= store.neighbors(f)
    neigh -= set(cands)
    fbest = {f: fused[file_chunks[f][0]] for f in file_rank}
    extras = sorted(neigh & set(file_rank), key=lambda f: -fbest[f])[:GRAPH_EXTRAS]

    if rerank and len(cands) > 1:
        # optional Haiku ORDER rerank v2: code evidence + 3-vote merge (+~2-3s)
        from .rerank2 import haiku_order2
        # deeper pool when reranking: the LLM can rescue rank 13-24
        deep = file_rank[:24]
        for f in deep:
            if f not in cands:
                cands.append(f)
        ev = [{"file": f, "code": metas[file_chunks[f][0]]["text"]} for f in cands]
        order = haiku_order2(query, ev)
        cands = [cands[i] for i in order]

    tier1 = cands[:TIER1_MAX]
    # adaptive CORE: only files within TIER1_GAP of the top get full code;
    # the rest demote to the map (bundle membership unchanged)
    top_score = fbest[tier1[0]]
    tier1 = [f for f in tier1 if fbest[f] >= top_score * TIER1_GAP] or tier1[:1]
    tier2 = [f for f in cands if f not in tier1] + extras

    out_t1 = []
    for f in tier1:
        idxs = file_chunks[f]
        best = fused[idxs[0]]
        keep = [i for i in idxs if fused[i] >= best * CHUNK_KEEP_RATIO][:12] or idxs[:1]
        keep.sort(key=lambda i: metas[i]["start_line"])
        out_t1.append({
            "file": f, "score": float(best),
            "chunks": [metas[i] | {"score": float(fused[i])} for i in keep],
            "symbols": store.symbols_for(f),
            "neighbors": sorted(store.neighbors(f) & set(cands + extras)),
        })
    out_t2 = []
    for f in tier2:
        idxs = file_chunks.get(f, [])
        matched = [metas[i]["name"] for i in idxs[:3] if metas[i]["name"]]
        syms = store.symbols_for(f)
        docline = next((s["doc"] for s in syms if s["doc"]), None)
        best_chunk = metas[idxs[0]] if idxs else None
        out_t2.append({
            "file": f, "score": float(fbest.get(f, 0)),
            "via_graph": f in extras, "matched": matched, "doc": docline,
            "best_chunk": best_chunk,
            "symbols": [s for s in syms if s["kind"] in OUTLINE_KINDS][:12],
        })
    return {"query": query, "tier1": out_t1, "tier2": out_t2,
            "repo": st.repo,
            "ms": int((time.time() - t0) * 1000)}


def search(root: Path, query: str, rerank: bool = False) -> dict:
    """One-shot retrieval (CLI/MCP entry). Builds state then queries — identical
    output to search_with_state(load_state(root), ...)."""
    return search_with_state(load_state(Path(root)), query, rerank)


def search_multi(roots: list[Path], query: str) -> dict:
    """Search several repos, merge by score (same embedder -> comparable).
    Files are prefixed repo-name/path. Tier1 capped at TIER1_MAX+2 across repos."""
    t0 = time.time()
    results = [search(r, query) for r in roots]
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
    promoted = t1[:TIER1_MAX + 2]
    demoted = [{"file": t["file"], "score": t["score"], "via_graph": False,
                "matched": [c["name"] for c in t["chunks"][:3] if c["name"]],
                "doc": None,
                "symbols": [s for s in t["symbols"]
                            if s["kind"] in OUTLINE_KINDS][:12]}
               for t in t1[TIER1_MAX + 2:]]
    return {"query": query, "tier1": promoted, "tier2": demoted + t2,
            "repo": "+".join(r["repo"] for r in results),
            "ms": int((time.time() - t0) * 1000)}


# ---------------------------------------------------------------- rendering


def lang_of(path: str) -> str:
    return {"py": "python", "ts": "typescript", "tsx": "tsx", "js": "javascript",
            "jsx": "jsx", "mjs": "javascript", "cjs": "javascript", "rb": "ruby",
            "go": "go", "md": "markdown", "markdown": "markdown", "mdx": "markdown"}.get(
        path.rsplit(".", 1)[-1], "")


def render(res: dict, compact: bool = False) -> str:
    L: list[str] = []
    n1, n2 = len(res["tier1"]), len(res["tier2"])
    L.append(f'# megabrain — "{res["query"]}"')
    L.append(f'repo `{res["repo"]}` · {n1} core files (full code) · {n2} related (mapped) · {res["ms"]}ms\n')

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
        L.append("## RELATED — matched code per file · expand with `megabrain get <file> [--symbol NAME]`\n")
        for t in res["tier2"]:
            via = " ·via-graph" if t["via_graph"] else ""
            match = f' · matched: {", ".join(t["matched"])}' if t["matched"] else ""
            doc = f' — {t["doc"]}' if t["doc"] else ""
            L.append(f'### {t["file"]}  `{t["score"]:.2f}`{via}{match}{doc}')
            bc = t.get("best_chunk")
            if bc and not compact:
                L.append(f'**{bc["name"] or bc["kind"]}** L{bc["start_line"]}-{bc["end_line"]}')
                L.append(f'```{lang_of(t["file"])}')
                L.append(bc["text"].rstrip("\n"))
                L.append("```")
            for s in t["symbols"][:6]:
                L.append(f'- `{s["signature"]}` L{s["line"]}-{s["end_line"]}')
            L.append("")
    return "\n".join(L)


def get_code(root: Path, relpath: str, symbol: str | None = None) -> str:
    p = Path(root) / relpath
    if not p.exists():
        return f"not found: {relpath}"
    src = p.read_text(errors="replace")
    if not symbol:
        return f"```{lang_of(relpath)}\n{src}\n```"
    store = Store(Path(root))
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
