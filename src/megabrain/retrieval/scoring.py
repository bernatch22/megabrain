"""Chunk scoring — the single source of truth.

score_chunks() computes the fused relevance of EVERY candidate chunk for a
query, without ranking or tiering. It runs a pipeline of self-gating LANES over
one shared QueryCtx, each mutating the `fused` score array in a fixed order:

    DenseFileFusionLane  dense chunk cosine fused with the file-vector score
    TestPenaltyLane      soft down-weight for test files
    IssueLane            long queries only: variant-ensemble RRF + BM25 sparse
                         entity-ID lane + deterministic traceback/identifier
                         grounding pins (tests fully masked)
    LexicalBoostLane     short dev queries only: exact filename/symbol token match

Each lane `applies(ctx)` (a cheap self-gate) and `apply(ctx, fused)`; adding a
signal is one new lane class + one LANES entry (OCP), never surgery on a
120-line function. The arithmetic and order are unchanged from the pre-lane
engine — tests/test_scoring_lanes.py pins the fused array bit-for-bit.

No LLM in this path: scoring is deterministic and embedding-only (locked rule
#1). Shared by search_with_state() and chunks_for_file().
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .params import RetrievalParams
from .state import SearchState

# directory names that mark a file as test/spec code wherever they appear in
# the path. Segment-exact (never substring: "src/contest/" is not a test dir).
# Vocabulary, not a tuning knob — the knobs live in params.RetrievalParams.
TEST_DIR_SEGS = frozenset({"test", "tests", "spec", "specs", "__tests__",
                           "testing", "fixtures"})


def _is_test_path(relpath: str) -> bool:
    """Test-file detector for the ranking down-weight. Two signals:
    any path segment named test/tests/spec/fixtures/… (repos use both singular
    and plural), or "test"/"spec" as a token-ish match in ANY segment
    (foo_test.go, test_foo.py, foo.spec.ts, mypyc/test-data/fixtures/ir.py —
    but not inspect.py/protest.py/src/contest)."""
    parts = relpath.lower().split("/")
    if any(p in TEST_DIR_SEGS for p in parts[:-1]):
        return True
    return any(re.search(r"(^|[._-])(test|spec)s?([._-]|$)",
                         p.rsplit(".", 1)[0]) for p in parts)


def under_path(relpath: str, path_filter: str) -> bool:
    """True when `relpath` is the filter file itself or lives under the filter
    directory (directory-boundary aware, so `src/dispatch` never matches
    `src/dispatcher.ts`). Empty filter matches everything."""
    if not path_filter:
        return True
    pf = path_filter.rstrip("/")
    return relpath == pf or relpath.startswith(pf + "/")


def apply_path_filter(metas: list, M: np.ndarray, path_filter: str | None):
    """Restrict the candidate chunk set to files under `path_filter`. Applied at
    the START of scoring so the ENTIRE bundle (CORE, RELATED, graph neighbors)
    stays within the sub-path. Fail-open: if the filter matches nothing, return
    the unfiltered set unchanged (a bad/stale subpath won't silently empty the
    bundle). Returns (metas, M) — untouched when no filter."""
    if not path_filter:
        return metas, M
    idx = [i for i, m in enumerate(metas) if under_path(m.file, path_filter)]
    if not idx:
        return metas, M
    return [metas[i] for i in idx], M[idx]


def filter_doc_chunks(metas: list, M: np.ndarray, doc_exts: tuple, keep: bool):
    """Restrict the candidate set to ONE side of the code/docs line BEFORE
    scoring, so the entire bundle (CORE, RELATED, graph neighbors) stays on it.

    keep=False — drop the docs: a doc titled like the query (+ its
    near-identical translations) can't crowd the code out of the bundle, which
    is what the code-only `ask` wants.
    keep=True — drop the CODE: the docs-only lane (`ask --docs`, `search
    --docs`, the studio's "Docs only" toggle). The question is about the prose,
    so code must not compete for the slots — post-filtering a mixed bundle
    instead would cap the answer at however many doc files happened to outrank
    the code.

    Fail-open BOTH ways: when the wanted side is empty (docs asked of a repo
    with no markdown, code asked of a docs-only repo) return the set unchanged
    rather than answering nothing."""
    idx = [i for i, m in enumerate(metas)
           if m.file.lower().endswith(doc_exts) == bool(keep)]
    if not idx or len(idx) == len(metas):
        return metas, M
    return [metas[i] for i in idx], M[idx]


def ident_tokens(text: str) -> set[str]:
    """Identifier-aware tokens: split camelCase/snake_case, len>=4 to avoid noise."""
    out = set()
    for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text):
        for p in re.split(r"_+", w):
            for s in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+", p):
                if len(s) >= 4:
                    out.add(s.lower())
    return out


@dataclass
class QueryCtx:
    """Everything the scoring lanes share for one query, computed once. The
    lanes read from here and mutate only the `fused` array threaded between
    them; nothing lane-specific leaks into this struct except the caches that
    already lived on SearchState (bm25 / issue symbol corpus / st.qv)."""
    st: SearchState
    query: str
    path_filter: str | None
    p: RetrievalParams
    metas: list                  # path-filtered, index-aligned with M
    M: np.ndarray                # path-filtered chunk matrix
    fpaths: list
    F: np.ndarray                # file matrix
    qv: np.ndarray               # query embedding (also stashed on st.qv)
    cfi: np.ndarray              # chunk -> file-row index (-1 if absent)
    is_test: np.ndarray          # per-chunk test-file mask
    qtok: set                    # identifier tokens of the query

    @property
    def store(self):
        return self.st.store

    @property
    def emb(self):
        return self.st.emb


class ScoreLane(Protocol):
    """A scoring signal. `applies` is a cheap self-gate (no-op when the query
    isn't this lane's kind); `apply` returns the new fused array. Recall-safe
    lanes never drop candidates, only reweight."""

    name: str

    def applies(self, ctx: QueryCtx) -> bool: ...

    def apply(self, ctx: QueryCtx, fused: np.ndarray | None) -> np.ndarray: ...


class DenseFileFusionLane:
    """Base relevance: dense chunk cosine fused with the file-skeleton cosine."""

    name = "dense+file"

    def applies(self, ctx: QueryCtx) -> bool:
        return True

    def apply(self, ctx: QueryCtx, fused: np.ndarray | None) -> np.ndarray:
        dense = (ctx.M @ ctx.qv + 1) / 2
        fscore = (ctx.F @ ctx.qv + 1) / 2
        return dense + ctx.p.file_fusion_w * np.where(ctx.cfi >= 0, fscore[ctx.cfi], 0.5)


class TestPenaltyLane:
    """Soft down-weight for test files: keep them reachable, stop them crowding."""

    name = "test-penalty"

    def applies(self, ctx: QueryCtx) -> bool:
        return True

    def apply(self, ctx: QueryCtx, fused: np.ndarray) -> np.ndarray:
        return np.where(ctx.is_test, fused * ctx.p.test_penalty, fused)


class IssueLane:
    """Long queries (bug reports/tracebacks): deterministic grounding — the BM25
    sparse entity-ID lane + a variant-ensemble RRF merge + traceback/identifier
    pins, with tests fully masked (LocAgent-style; gold files are never tests)."""

    name = "issue"

    def applies(self, ctx: QueryCtx) -> bool:
        return len(ctx.qtok) > ctx.p.issue_token_threshold

    def apply(self, ctx: QueryCtx, fused: np.ndarray) -> np.ndarray:
        st, p, metas, M, F = ctx.st, ctx.p, ctx.metas, ctx.M, ctx.F
        fpaths, cfi, is_test = ctx.fpaths, ctx.cfi, ctx.is_test
        store, emb, query = ctx.store, ctx.emb, ctx.query
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
        if ctx.path_filter is None and st.issue_syms is not None:
            all_files, all_syms = st.issue_files, st.issue_syms
        else:
            all_files = list(dict.fromkeys(m.file for m in metas))
            all_syms = []
            for f in all_files:
                for s in store.symbols_for(f):
                    all_syms.append({"file": f, "name": s["name"],
                                     "line": s["line"], "end_line": s["end_line"]})
            if ctx.path_filter is None:
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
        return fused


class LexicalBoostLane:
    """Short dev queries only: exact filename/symbol token match -> additive
    boost. Long texts (issue reports) carry so many identifier tokens the boost
    becomes uniform noise, so it self-gates OFF there. (BM25 is deliberately NOT
    blended into short queries — it raised SWE recall but cost golden bundle
    completeness; it stays in issue-mode RRF only.)"""

    name = "lexical"

    def applies(self, ctx: QueryCtx) -> bool:
        return bool(ctx.qtok) and len(ctx.qtok) <= ctx.p.issue_token_threshold

    def apply(self, ctx: QueryCtx, fused: np.ndarray) -> np.ndarray:
        p, qtok = ctx.p, ctx.qtok
        boost = np.zeros(len(ctx.metas))
        for i, m in enumerate(ctx.metas):
            stem = m.file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            nf = len(ident_tokens(stem) & qtok)
            ns = len(ident_tokens(m.name or "") & qtok)
            boost[i] = max(p.file_boost_w * min(nf, p.lexical_boost_cap),
                           p.sym_boost_w * min(ns, p.lexical_boost_cap))
        return fused + boost


# The scoring pipeline — ordered. Adding a signal = one lane class + one entry.
# Order is load-bearing (test penalty before issue masking; lexical last).
LANES: tuple[ScoreLane, ...] = (
    DenseFileFusionLane(), TestPenaltyLane(), IssueLane(), LexicalBoostLane())


def score_chunks(st: SearchState, query: str,
                 path_filter: str | None = None,
                 exclude_docs: bool = False,
                 only_docs: bool = False) -> tuple[list, np.ndarray]:
    """Full per-chunk scoring (dense + file-fusion + test penalty + issue-mode /
    lexical boosts) WITHOUT ranking/tiering, run as the LANES pipeline over one
    QueryCtx. Returns (path-filtered metas, fused score array), index-aligned.
    Single source of truth for chunk scoring — shared by search_with_state()
    and chunks_for_file(). The content lane is a tri-state: neither flag =
    everything, `exclude_docs` = code only (the default ask's retrieval),
    `only_docs` = markdown only (the docs-only lane). `only_docs` wins if both
    are passed; both are fail-open (see filter_doc_chunks)."""
    if not st.metas:
        from ..errors import EmptyIndex
        raise EmptyIndex.at()
    # PATH-SCOPE: restrict candidates to files under the sub-path BEFORE scoring,
    # so CORE/RELATED/graph-neighbors all stay within it. No filter -> unchanged.
    metas, M = apply_path_filter(st.metas, st.M, path_filter)
    if exclude_docs or only_docs:
        from ..indexing.strategies import MarkdownStrategy
        metas, M = filter_doc_chunks(metas, M, tuple(MarkdownStrategy.exts),
                                     keep=only_docs)
    qv = st.emb.embed([query])[0]
    st.qv = qv                     # re-used by the flow lane (no second embed)
    f2i = {f: i for i, f in enumerate(st.fpaths)}
    ctx = QueryCtx(
        st=st, query=query, path_filter=path_filter, p=st.params,
        metas=metas, M=M, fpaths=st.fpaths, F=st.F, qv=qv,
        cfi=np.array([f2i.get(m.file, -1) for m in metas]),
        is_test=np.array([_is_test_path(m.file) for m in metas]),
        qtok=ident_tokens(query))
    fused: np.ndarray | None = None
    for lane in LANES:
        if lane.applies(ctx):
            fused = lane.apply(ctx, fused)
    return metas, fused
