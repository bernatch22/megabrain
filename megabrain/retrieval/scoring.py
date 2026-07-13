"""Chunk scoring — the single source of truth.

score_chunks() computes the fused relevance of EVERY candidate chunk for a
query, without ranking or tiering. The lanes: dense cosine fused with the
file-vector score, a soft test-file penalty, issue mode for long queries
(variant-ensemble RRF + the BM25 sparse entity-ID lane + deterministic
traceback/identifier grounding pins), and the exact-token lexical boost for
short developer queries. Shared by search_with_state() and chunks_for_file()
— keep the scoring semantics here and nowhere else. No LLM in this path:
scoring is deterministic and embedding-only (locked rule #1).
"""

from __future__ import annotations

import re

import numpy as np

from .state import SearchState

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


# public name (ask_agents uses it); the underscore spelling stays for
# backward compatibility with older imports of the pre-split query module.
ident_tokens = _ident_tokens


def score_chunks(st: SearchState, query: str,
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
