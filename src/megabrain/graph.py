"""megabrain_graph — the repo as a navigable knowledge graph. numpy-only.

Where graphify needs LLM sub-agents to extract relationships, megabrain already
owns them: the AST import/call edges (storage `edges` table) are the STRUCTURAL
lane, and the per-file skeleton embeddings add a SEMANTIC lane (cosine — files
that talk about the same thing without importing each other; what graphify tags
INFERRED, but with an honest score and zero LLM). On top of that combined
graph:

  communities   deterministic weighted label propagation (numpy, no networkx —
                PageRank was rejected by experiment for RANKING; this is
                STRUCTURE, a different use, and label prop is parameter-free)
  god nodes     highest structural degree — the repo's core abstractions
  surprises     high semantic similarity + no structural edge + different
                communities — connections you didn't know were there
  paths         BFS between two concepts, endpoints resolved by EMBEDDING
                (beats lexical matching for "the scoring pipeline" → scoring.py)

The ONLY LLM in this module is community labeling (one buffered call, cached in
the store's meta table under a graph fingerprint, fail-open to "Community N").
Everything else is deterministic. Node views splice the REAL chunks from the
store — the graph never paraphrases code.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .retrieval.scoring import _is_test_path, under_path
from .retrieval.state import SearchState, load_state

log = logging.getLogger(__name__)

SEM_EDGE_MIN = 0.80      # min cosine for a semantic edge
SEM_TOP_K = 3            # semantic edges per node cap (keeps the graph sparse)
SEM_WEIGHT = 0.5         # label-prop weight of a semantic edge (struct = 1.0)
SURPRISE_MIN = 0.85      # surprises need to be MORE similar than a mere edge
LABEL_MAX_TOKENS = 500
LABEL_TIMEOUT = 45
MAX_LP_ITERS = 50


@dataclass
class RepoGraph:
    """The combined structural+semantic graph over one repo's files."""
    files: list[str]
    idx: dict[str, int]
    struct: list[dict[int, set[str]]]          # i -> {j: {kinds}} (undirected view)
    out_deg: np.ndarray                        # directed degrees for god-node split
    in_deg: np.ndarray
    sem: list[dict[int, float]]                # i -> {j: cosine} (undirected)
    S: np.ndarray | None = None                # full cosine matrix (surprises)
    comm: dict[str, int] = field(default_factory=dict)


def build_graph(st: SearchState, path_filter: str | None = None) -> RepoGraph:
    files = [f for f in st.fpaths if under_path(f, path_filter or "")]
    idx = {f: i for i, f in enumerate(files)}
    n = len(files)
    struct: list[dict[int, set[str]]] = [defaultdict(set) for _ in range(n)]
    out_deg, in_deg = np.zeros(n), np.zeros(n)
    for s, d, k in st.store.all_edges():
        i, j = idx.get(s), idx.get(d)
        if i is None or j is None or i == j:
            continue
        if j not in struct[i]:
            out_deg[i] += 1
            in_deg[j] += 1
        struct[i][j].add(k)
        struct[j][i].add(k)

    # semantic lane: skeleton-vector cosine, top-k per node above the floor
    sem: list[dict[int, float]] = [dict() for _ in range(n)]
    S = None
    if n > 1:
        if path_filter:
            pos = {f: i for i, f in enumerate(st.fpaths)}
            F = st.F[[pos[f] for f in files]]
        else:
            F = st.F
        norms = np.linalg.norm(F, axis=1, keepdims=True)
        Fn = F / np.where(norms == 0, 1, norms)
        S = Fn @ Fn.T
        np.fill_diagonal(S, -1.0)
        for i in range(n):
            top = np.argsort(-S[i])[:SEM_TOP_K]
            for j in top:
                sc = float(S[i, j])
                if sc >= SEM_EDGE_MIN:
                    sem[i][int(j)] = max(sem[i].get(int(j), 0), sc)
                    sem[int(j)][i] = max(sem[int(j)].get(i, 0), sc)

    g = RepoGraph(files=files, idx=idx, struct=struct,
                  out_deg=out_deg, in_deg=in_deg, sem=sem, S=S)
    g.comm = _communities(g)
    return g


def _communities(g: RepoGraph) -> dict[str, int]:
    """Deterministic weighted label propagation. Fixed ascending visit order +
    smallest-label tie-break -> byte-stable across runs. Communities renumbered
    by size (0 = largest)."""
    n = len(g.files)
    labels = np.arange(n)
    for _ in range(MAX_LP_ITERS):
        changed = False
        for i in range(n):
            w: dict[int, float] = defaultdict(float)
            for j, kinds in g.struct[i].items():
                w[labels[j]] += 1.0 * len(kinds)
            for j, sc in g.sem[i].items():
                w[labels[j]] += SEM_WEIGHT * sc
            if not w:
                continue
            best = min(sorted(w), key=lambda lb: (-w[lb], lb))
            if best != labels[i]:
                labels[i] = best
                changed = True
        if not changed:
            break
    sizes = defaultdict(int)
    for lb in labels:
        sizes[int(lb)] += 1
    order = sorted(sizes, key=lambda lb: (-sizes[lb], lb))
    renum = {lb: k for k, lb in enumerate(order)}
    return {g.files[i]: renum[int(labels[i])] for i in range(n)}


def _fingerprint(g: RepoGraph) -> str:
    edge_n = int(sum(len(d) for d in g.struct) // 2)
    sem_n = int(sum(len(d) for d in g.sem) // 2)
    h = hashlib.sha1(json.dumps([sorted(g.files), edge_n, sem_n,
                                 SEM_EDGE_MIN, SEM_TOP_K]).encode())
    return h.hexdigest()


def god_nodes(g: RepoGraph, k: int = 10) -> list[dict]:
    deg = [(len(g.struct[i]), i) for i in range(len(g.files))]
    deg.sort(key=lambda t: (-t[0], g.files[t[1]]))
    return [{"file": g.files[i], "degree": d,
             "out": int(g.out_deg[i]), "in": int(g.in_deg[i]),
             "community": g.comm[g.files[i]]}
            for d, i in deg[:k] if d > 0]


def surprises(g: RepoGraph, k: int = 10) -> list[dict]:
    """High semantic similarity, NO structural edge, different communities —
    graphify's cross-document surprise, scored honestly."""
    if g.S is None:
        return []
    out = []
    n = len(g.files)
    for i in range(n):
        for j in range(i + 1, n):
            sc = float(g.S[i, j])
            if (sc >= SURPRISE_MIN and j not in g.struct[i]
                    and g.comm[g.files[i]] != g.comm[g.files[j]]):
                out.append({"a": g.files[i], "b": g.files[j],
                            "score": round(sc, 3),
                            "a_community": g.comm[g.files[i]],
                            "b_community": g.comm[g.files[j]]})
    out.sort(key=lambda s: -s["score"])
    return out[:k]


def resolve_node(st: SearchState, g: RepoGraph, term: str) -> str | None:
    """Term -> file. Exact/suffix path match wins; else EMBED the term and take
    the closest skeleton (concept search: "the scoring pipeline" -> scoring.py).

    Test files carry the SAME soft down-weight retrieval applies
    (`params.test_penalty`): a test's skeleton is full of the vocabulary of the
    thing it tests, so raw cosine sends "the studio web server" to
    test_serve_api_ui.py. They stay reachable — name one explicitly and the
    path match above wins before any embedding runs."""
    t = term.strip().strip("/")
    if t in g.idx:
        return t
    tails = [f for f in g.files if f.endswith("/" + t) or Path(f).name == t]
    if len(tails) >= 1:
        return sorted(tails)[0]
    if not g.files:
        return None
    pos = {f: i for i, f in enumerate(st.fpaths)}
    F = st.F[[pos[f] for f in g.files]]
    qv = st.emb.embed([term])[0]
    norms = np.linalg.norm(F, axis=1)
    sims = (F @ qv) / np.where(norms == 0, 1, norms)
    penalty = np.array([st.params.test_penalty if _is_test_path(f) else 1.0
                        for f in g.files])
    return g.files[int(np.argmax(sims * penalty))]


def shortest_path(g: RepoGraph, src: str, dst: str) -> list[dict]:
    """BFS over struct+semantic edges. Each hop says what carries it.

    Structural edges are expanded before semantic ones, and non-test files
    before tests (same house rule as ranking: tests stay reachable, they just
    never crowd). At equal distance that yields the route through real code —
    a test file bridges half the repo without explaining anything."""
    if src not in g.idx or dst not in g.idx:
        return []
    a, b = g.idx[src], g.idx[dst]
    order = lambda j: (_is_test_path(g.files[j]), g.files[j])  # noqa: E731
    prev: dict[int, tuple[int, str]] = {a: (-1, "")}
    frontier = [a]
    while frontier and b not in prev:
        nxt = []
        for i in frontier:
            for j in sorted(g.struct[i], key=order):
                if j not in prev:
                    prev[j] = (i, "/".join(sorted(g.struct[i][j])))
                    nxt.append(j)
            for j in sorted(g.sem[i], key=order):
                if j not in prev:
                    prev[j] = (i, f"semantic {g.sem[i][j]:.2f}")
                    nxt.append(j)
        frontier = nxt
    if b not in prev:
        return []
    hops, cur = [], b
    while cur != a:
        p, via = prev[cur]
        hops.append({"file": g.files[cur], "via": via})
        cur = p
    hops.append({"file": g.files[a], "via": ""})
    return list(reversed(hops))


def label_communities(st: SearchState, g: RepoGraph) -> dict[int, str]:
    """The one LLM touch: 2-4 word names per community, cached in meta under
    the graph fingerprint. Fail-open to 'Community N'."""
    cids = sorted(set(g.comm.values()))
    fallback = {c: f"Community {c}" for c in cids}
    fp = _fingerprint(g)
    cached = st.store.get_meta("graph_labels")
    if cached and cached.get("fp") == fp:
        return {int(k): v for k, v in cached["labels"].items()}
    by_c: dict[int, list[str]] = defaultdict(list)
    for f, c in g.comm.items():
        by_c[c].append(f)
    lines = []
    for c in cids:
        fs = sorted(by_c[c], key=lambda f: -len(g.struct[g.idx[f]]))[:8]
        syms = []
        for f in fs[:3]:
            syms += [s["name"] for s in st.store.symbols_for(f)[:4]]
        lines.append(f'{c}: files={", ".join(fs)} · symbols={", ".join(syms[:10])}')
    prompt = ("Name each code-community with a 2-4 word plain label (what the "
              "code DOES, e.g. \"Retrieval scoring\", \"HTTP server\").\n"
              "Communities:\n" + "\n".join(lines) +
              '\n\nReturn ONLY a JSON object {"0": "label", ...} for every id.')
    try:
        from . import providers
        reply = providers.chat_text(providers.ask_model(), prompt,
                                    LABEL_MAX_TOKENS, timeout=LABEL_TIMEOUT)
        m = re.search(r"\{.*\}", reply, re.S)
        labels = {int(k): str(v)[:60] for k, v in json.loads(m.group(0)).items()
                  if int(k) in fallback}
        out = {**fallback, **labels}
        st.store.set_meta("graph_labels", {"fp": fp,
                                           "labels": {str(k): v for k, v in out.items()}})
        st.store.commit()
        return out
    except Exception:
        log.debug("community labeling failed open", exc_info=True)
        return fallback


# ── the three modes ────────────────────────────────────────────────────────

def graph_map(st: SearchState, path_filter: str | None = None,
              label: bool = True) -> dict:
    t0 = time.time()
    g = build_graph(st, path_filter)
    labels = label_communities(st, g) if label else \
        {c: f"Community {c}" for c in set(g.comm.values())}
    by_c: dict[int, list[str]] = defaultdict(list)
    for f, c in g.comm.items():
        by_c[c].append(f)
    links, seen = [], set()
    for i in range(len(g.files)):
        for j, kinds in g.struct[i].items():
            if (min(i, j), max(i, j)) not in seen:
                seen.add((min(i, j), max(i, j)))
                links.append({"s": g.files[i], "d": g.files[j],
                              "kind": "/".join(sorted(kinds))})
        for j, sc in g.sem[i].items():
            if (min(i, j), max(i, j), "sem") not in seen:
                seen.add((min(i, j), max(i, j), "sem"))
                links.append({"s": g.files[i], "d": g.files[j],
                              "kind": "semantic", "score": round(sc, 3)})
    return {
        "repo": st.repo, "files": len(g.files),
        "communities": [{"id": c, "label": labels.get(c, f"Community {c}"),
                         "size": len(fs),
                         "files": sorted(fs, key=lambda f: -len(g.struct[g.idx[f]]))}
                        for c, fs in sorted(by_c.items())],
        "god_nodes": god_nodes(g),
        "surprises": surprises(g),
        "nodes": [{"file": f, "community": g.comm[f],
                   "degree": len(g.struct[g.idx[f]])} for f in g.files],
        "links": links,
        "ms": int((time.time() - t0) * 1000),
    }


def graph_node(st: SearchState, term: str,
               path_filter: str | None = None, label: bool = True) -> dict:
    t0 = time.time()
    g = build_graph(st, path_filter)
    f = resolve_node(st, g, term)
    if f is None:
        from .errors import MegabrainError
        raise MegabrainError(f"no file matches {term!r}")
    i = g.idx[f]
    out_e, in_e = [], []
    for s, d, k in st.store.all_edges():
        if s == f and d in g.idx:
            out_e.append({"file": d, "kind": k})
        elif d == f and s in g.idx:
            in_e.append({"file": s, "kind": k})
    labels = label_communities(st, g) if label else \
        {c: f"Community {c}" for c in set(g.comm.values())}
    return {
        "repo": st.repo, "file": f, "resolved_from": term,
        "community": {"id": g.comm[f], "label": labels.get(g.comm[f])},
        "degree": len(g.struct[i]),
        "out": sorted(out_e, key=lambda e: e["file"]),
        "in": sorted(in_e, key=lambda e: e["file"]),
        "semantic": sorted(({"file": g.files[j], "score": round(sc, 3)}
                            for j, sc in g.sem[i].items()),
                           key=lambda e: -e["score"]),
        "symbols": st.store.symbols_for(f),
        "chunks": st.store.file_chunks(f),
        "ms": int((time.time() - t0) * 1000),
    }


_CALLABLE_KINDS = {"class", "function", "async_function", "method",
                   "async_method", "interface", "type", "enum"}


def _source(st: SearchState, rel: str) -> str:
    """The file's real bytes (disk first; the chunk partition reconstructs it
    when the file moved out from under the index)."""
    try:
        return (Path(st.store.root) / rel).read_text(encoding="utf-8",
                                                     errors="replace")
    except OSError:
        return "\n".join(c["text"] or "" for c in st.store.file_chunks(rel))


def _py_uses(source: str):
    """ast walk -> (alias_map, uses). `uses` is name -> [(line, receiver)]
    where receiver is the `x` of an `x.name(...)` call (None for plain calls
    and import sites). `alias_map` maps every imported alias to the dotted
    module it denotes — the receiver check below uses it to reject stdlib/
    external attribute calls (`re.search(...)` once 'connected' rerank.py to
    bundle.py's `search()`). None = not parseable (lexical fallback)."""
    import ast as _ast
    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return None
    aliases: dict[str, tuple[int, str]] = {}     # alias -> (level, dotted)
    calls: dict[str, list[tuple[int, str | None]]] = {}
    imports: dict[str, list[tuple[int, str | None]]] = {}
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Call):
            f = node.func
            if isinstance(f, _ast.Name):
                calls.setdefault(f.id, []).append((node.lineno, None))
            elif isinstance(f, _ast.Attribute):
                v = f.value              # x.name(...) and Cls(...).name(...)
                recv = v.id if isinstance(v, _ast.Name) else (
                    v.func.id if isinstance(v, _ast.Call)
                    and isinstance(v.func, _ast.Name) else None)
                calls.setdefault(f.attr, []).append((node.lineno, recv))
        elif isinstance(node, _ast.Import):
            for a in node.names:
                aliases[a.asname or a.name.split(".")[0]] = (0, a.name)
        elif isinstance(node, _ast.ImportFrom):
            mod = node.module or ""
            for a in node.names:
                imports.setdefault(a.name, []).append((node.lineno, None))
                dotted = f"{mod}.{a.name}" if mod else a.name
                aliases[a.asname or a.name] = (node.level, dotted)
    byline = lambda t: t[0]              # noqa: E731 — (line, recv|None) tuples
    uses = {n: sorted(calls.get(n, []), key=byline)
            + sorted(imports.get(n, []), key=byline)
            for n in {*calls, *imports}}
    return aliases, uses


def _alias_files(st: SearchState, use_file: str,
                 level: int, dotted: str) -> set[str] | None:
    """Repo files an imported alias could denote (module.py or its package
    __init__). None = nothing in the repo matches -> the alias is external
    (stdlib, site-packages) and can never carry an in-repo connection."""
    def _match(parts: list[str]) -> set[str]:
        if not parts:
            return set()
        if level:                        # relative: anchor at the file's package
            base = list(Path(use_file).parent.parts)
            base = base[:len(base) - (level - 1)] if level > 1 else base
            cands = {"/".join(base + parts) + ".py",
                     "/".join(base + parts) + "/__init__.py"}
            return {f for f in st.fpaths if f in cands}
        suffix = "/".join(parts)         # absolute: match by dotted-path suffix
        return {f for f in st.fpaths
                if f == suffix + ".py" or f.endswith("/" + suffix + ".py")
                or f == suffix + "/__init__.py"
                or f.endswith("/" + suffix + "/__init__.py")}

    parts = [p for p in dotted.split(".") if p]
    # `from mod import Class` aliases don't name a module file — fall back to
    # the parent module, so `Store(x).get_meta()` still resolves to store.py
    # (and `Path(x).resolve()` still resolves to NOTHING -> rejected).
    hit = _match(parts) or _match(parts[:-1])
    return hit or None


def _use_sites(st: SearchState, uses_file: str, names: set[str],
               defs_file: str) -> dict[str, list[int]]:
    """name -> line numbers where `uses_file` REALLY uses a name defined in
    `defs_file`. Python goes through the ast, and attribute calls are
    receiver-checked: `alias.name(...)` counts only when the alias resolves to
    `defs_file` itself; an alias resolving elsewhere (or to nothing in the
    repo — stdlib) is rejected. Unresolvable receivers (variables, self) stay
    counted, as before. Other content: word-boundary scan, no line info."""
    src = _source(st, uses_file)
    if uses_file.endswith(".py"):
        parsed = _py_uses(src)
        if parsed is not None:
            aliases, uses = parsed
            out: dict[str, dict] = {}
            for n in names:
                lines, strong = [], False
                for ln, recv in uses.get(n, ()):
                    if recv is None:     # plain call / import site: verified
                        lines.append(ln)
                        strong = True
                    elif recv in aliases:
                        files = _alias_files(st, uses_file, *aliases[recv])
                        if files is None or defs_file not in files:
                            continue     # external or a DIFFERENT module
                        lines.append(ln)
                        strong = True    # alias resolves to the defs file
                    else:                # variable/self receiver: inferred only
                        lines.append(ln)
                if lines:
                    out[n] = {"lines": lines, "strong": strong}
            return out
    out = {}
    for n in names:
        if re.search(rf"\b{re.escape(n)}\b", src):
            out[n] = {"lines": [], "strong": False}
    return out


def _hop_symbols(st: SearchState, prev: str, cur: str, cap: int = 4) -> list[str]:
    """The SYMBOLS that carry a hop: names defined in one file of the pair and
    ACTUALLY USED (called/imported — ast, not word-matching) in the other.
    Both directions, since BFS walks edges undirected. Ordered by call count."""
    counts: dict[str, int] = {}
    for defs_file, uses_file in ((cur, prev), (prev, cur)):
        names = {s["name"].split(".")[-1] for s in st.store.symbols_for(defs_file)
                 if s["name"] and s["kind"] in _CALLABLE_KINDS
                 and len(s["name"].split(".")[-1]) >= 3}
        if not names:
            continue
        for name, u in _use_sites(st, uses_file, names, defs_file).items():
            # verified sites (plain calls, resolved aliases) outrank inferred
            # variable-receiver matches 10:1 — `dict.get` noise never buries
            # a real carrier
            w = (10 if u["strong"] else 1) * max(1, len(u["lines"]))
            counts[name] = counts.get(name, 0) + w
    return [n for n, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:cap]


SNIP_LINES = 22


def _snip(chunks: list[dict], symbol: str, at_line: int | None = None,
          at_lines: list[int] | None = None) -> dict | None:
    """A small window of REAL chunk text around a KNOWN line — the def line or
    the ast-verified call sites — falling back to the first word match only
    when no line info exists. `hi_rows` = window-relative rows to highlight
    (the exact call/def lines, so the UI never marks a same-named local)."""
    pat = re.compile(rf"\b{re.escape(symbol)}\b")
    anchor = at_line if at_line is not None else (at_lines[0] if at_lines else None)
    for c in chunks:
        lines = (c["text"] or "").splitlines()
        row = None
        if anchor is not None:
            if c["start_line"] <= anchor <= c["end_line"]:
                row = anchor - c["start_line"]
        else:
            row = next((i for i, ln in enumerate(lines) if pat.search(ln)), None)
        if row is None:
            continue
        lo = max(0, row - SNIP_LINES // 3)
        hi = min(len(lines), lo + SNIP_LINES)
        start = c["start_line"] + lo
        marks = [ln - start for ln in (at_lines or ([anchor] if anchor else []))
                 if start <= ln < start + (hi - lo)]
        return {"start_line": start, "text": "\n".join(lines[lo:hi]),
                "hi": symbol, "hi_rows": sorted(set(marks))}
    return None


def _enclosing_symbol(st: SearchState, rel: str, line: int) -> str | None:
    """The innermost def/class containing `line` — the story's connective
    tissue: a call site means nothing without knowing WHOSE body it's in."""
    best = None
    for s in st.store.symbols_for(rel):
        end = s["end_line"] or s["line"]
        if s["line"] <= line <= end and (best is None or s["line"] > best["line"]):
            best = s
    return best["name"].split(".")[-1] if best else None


def _hop_code(st: SearchState, prev: str, cur: str,
              symbols: list[str]) -> dict | None:
    """USE + DEF snippets for a hop's top carrier symbol: the ast-verified
    call sites in one file (tagged with their ENCLOSING function), the
    definition in the other (whichever direction the edge actually runs)."""
    for sym in symbols:                  # first carrier that has a real def
        for def_file, use_file in ((cur, prev), (prev, cur)):
            d = next((s for s in st.store.symbols_for(def_file)
                      if s["name"].split(".")[-1] == sym), None)
            if d is None:
                continue
            u = _use_sites(st, use_file, {sym}, def_file).get(sym)
            sites = (u or {}).get("lines") or []
            use = _snip(st.store.file_chunks(use_file), sym, at_lines=sites)
            if use and sites:
                use["in_symbol"] = _enclosing_symbol(st, use_file, sites[0])
            dfn = _snip(st.store.file_chunks(def_file), sym, at_line=d["line"])
            if use or dfn:
                return {"symbol": sym,
                        "verified": bool(u and u["strong"]),
                        "use": {**use, "file": use_file} if use else None,
                        "def": {**dfn, "file": def_file} if dfn else None}
    return None


def _orient_hops(st: SearchState, hops: list[dict]) -> tuple[list[dict], bool]:
    """Present the route in CALL-FLOW order. The graph is undirected, so a
    query phrased against the flow ("scoring -> narrator") walks every call
    backwards and the story reads wrong (the real chain is narrator -> agents
    -> scoring). If most directed edges point opposite to the walk, flip the
    presentation — caller first, descending the call stack — and say so."""
    if len(hops) < 2:
        return hops, False
    edges = {(s, d) for s, d, _ in st.store.all_edges()}
    fwd = back = 0
    for k in range(1, len(hops)):
        a, b = hops[k - 1]["file"], hops[k]["file"]
        fwd += (a, b) in edges
        back += (b, a) in edges
    if back <= fwd:
        return hops, False
    m = len(hops) - 1
    rev = []
    for k in range(len(hops)):
        nh = {"file": hops[m - k]["file"]}
        if k:                            # the entering edge's via moves with it
            nh["via"] = hops[m - k + 1]["via"]
        else:
            nh["via"] = ""
        rev.append(nh)
    return rev, True


def graph_path(st: SearchState, source: str, target: str,
               path_filter: str | None = None) -> dict:
    t0 = time.time()
    g = build_graph(st, path_filter)
    a, b = resolve_node(st, g, source), resolve_node(st, g, target)
    hops = shortest_path(g, a, b) if a and b else []
    hops, flipped = _orient_hops(st, hops)
    for k in range(1, len(hops)):        # what functions/classes carry each hop
        syms = _hop_symbols(st, hops[k - 1]["file"], hops[k]["file"])
        hops[k]["symbols"] = syms
        hops[k]["code"] = _hop_code(st, hops[k - 1]["file"], hops[k]["file"], syms)
    # is this an actual CALL CHAIN, or do the endpoints merely MEET somewhere?
    # scoring -> http <- rerank is not a flow: both call INTO http and never
    # into each other. Directions come from each hop's use/def sides.
    dirs = []
    for k in range(1, len(hops)):
        c = hops[k].get("code") or {}
        if c.get("use") and c.get("def"):
            dirs.append("fwd" if c["def"]["file"] == hops[k]["file"] else "back")
        else:
            dirs.append(None)
    known = [d for d in dirs if d]
    chain = not ("fwd" in known and "back" in known)
    meet = None
    if not chain:
        for i in range(len(dirs) - 1):
            if dirs[i] == "fwd" and dirs[i + 1] == "back":
                meet = hops[i + 1]["file"]      # both arrows point at this node
                break
    return {"repo": st.repo,
            "source": hops[0]["file"] if hops else a,
            "target": hops[-1]["file"] if hops else b,
            "resolved_from": [source, target], "flipped": flipped,
            "chain": chain, "meet": meet,
            "found": bool(hops), "hops": hops,
            "ms": int((time.time() - t0) * 1000)}


def graph_root(root: Path, mode: str = "map", node: str | None = None,
               source: str | None = None, target: str | None = None,
               path_filter: str | None = None, label: bool = True) -> dict:
    """One-shot entry (CLI/MCP): build state, run one mode."""
    from .errors import MegabrainError
    with load_state(Path(root)) as st:
        if mode == "node":
            if not node:
                raise MegabrainError("graph mode=node needs `node`")
            return graph_node(st, node, path_filter, label=label)
        if mode == "path":
            if not (source and target):
                raise MegabrainError("graph mode=path needs `source` and `target`")
            return graph_path(st, source, target, path_filter)
        return graph_map(st, path_filter, label=label)


# ── render (CLI/MCP text view) ─────────────────────────────────────────────

def render_graph(res: dict) -> str:
    L: list[str] = []
    if "communities" in res:                                   # map
        L.append(f'# megabrain graph — `{res["repo"]}` · {res["files"]} files '
                 f'· {len(res["links"])} links · {res["ms"]}ms')
        for c in res["communities"]:
            fs = c["files"]
            L.append(f'\n## [{c["id"]}] {c["label"]} — {c["size"]} files')
            L.append("  " + " · ".join(fs[:8]) + (" · …" if len(fs) > 8 else ""))
        if res["god_nodes"]:
            L.append("\n## god nodes (core abstractions)")
            for n in res["god_nodes"]:
                L.append(f'  {n["file"]:<44} deg={n["degree"]} '
                         f'(out {n["out"]} / in {n["in"]})')
        if res["surprises"]:
            L.append("\n## surprising connections (similar, unlinked, cross-community)")
            for s in res["surprises"]:
                L.append(f'  {s["a"]}  ~{s["score"]}~  {s["b"]}')
    elif "hops" in res:                                        # path
        flip = " · shown in call-flow order (flipped from your query)" \
            if res.get("flipped") else ""
        L.append(f'# graph path — {res["source"]} → {res["target"]} · {res["ms"]}ms{flip}')
        if not res["found"]:
            L.append("no path found")
        elif res.get("chain") is False:
            L.append(f'⚠ NOT a call chain — the endpoints never call each other; '
                     f'both connect INTO {res.get("meet") or "a shared file"}')
        for h in res["hops"]:
            syms = f'  · via {", ".join(h["symbols"])}' if h.get("symbols") else ""
            L.append(f'  {"└─ " + h["via"] + " → " if h["via"] else ""}{h["file"]}{syms}')
    else:                                                      # node
        c = res["community"]
        L.append(f'# graph node — {res["file"]} · [{c["id"]}] {c["label"]} '
                 f'· degree {res["degree"]} · {res["ms"]}ms')
        if res["out"]:
            L.append("\n## outgoing")
            for e in res["out"]:
                L.append(f'  → {e["file"]}  ({e["kind"]})')
        if res["in"]:
            L.append("\n## incoming")
            for e in res["in"]:
                L.append(f'  ← {e["file"]}  ({e["kind"]})')
        if res["semantic"]:
            L.append("\n## semantically close (no structural edge needed)")
            for e in res["semantic"]:
                L.append(f'  ~ {e["file"]}  ({e["score"]})')
        if res["symbols"]:
            L.append("\n## symbols")
            for s in res["symbols"][:20]:
                L.append(f'  {s["signature"] or s["name"]}  L{s["line"]}')
        for ch in res["chunks"]:
            L.append(f'\n### [{ch["id"]}] {ch["name"] or ch["kind"]} '
                     f'L{ch["start_line"]}-{ch["end_line"]}')
            from .retrieval.render import lang_of
            L.append(f'```{lang_of(res["file"])}')
            L.append((ch["text"] or "").rstrip("\n"))
            L.append("```")
    return "\n".join(L)
