"""specialize — hand-written chunkers tuned to a repo's own conventions.

NO LLM. An earlier version had an LLM generate specialization strategies; across
four repos (sinatra, requests, sdk-server, the engine itself) the generated
chunkers consistently LOST to a five-line deterministic recipe (the AST chunker
re-budgeted to 2000, `lit_baseline`) and to the plain default — so that path was
removed. The lesson: the engine's own tree-sitter chunkers are better than any
cut logic an LLM writes in a prompt.

What survives is a measurement toolkit for chunkers a human writes into
`<repo>/.megabrain/strategies/`:
  - detect_specialization — where the built-in chunks poorly (data tables,
    blobs, line-window fallback), deterministic, no LLM.
  - lit_baseline — the literature-tuned reference a candidate must beat
    (2000-char budget, arxiv 2605.04763; tests/specs/docs delegate to 4000).
  - gate_strategy — measure a hand-written strategy against that baseline with
    forge_eval.ab_gate and install it (trust-gated) only if it wins.

Hard-won caveat, kept here so no one re-litigates it: tighter chunks improve
span-IoU (navigation — less to read) but on a real query set (the sdk-server
golden) they LOWER retrieval ranking, because the 4000 merge concentrates a
file's evidence and that is what wins R@1. So a specialization is an honest win
only for its navigation objective; the engine default stays 4000.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from .forge import install
from .indexing.indexer import EXCLUDE_DIRS, MAX_FILE_BYTES, load_ignore
from .indexing.strategies import build_registry, strategy_for

log = logging.getLogger(__name__)

BUDGET = 4000
BLOB_FRAC = 0.55           # largest chunk this share of a file's chars = a blob
MIN_LINES = 120            # only large files are worth specializing
MAX_OPP_FILES = 40         # cap files shown per opportunity


# ---------------------------------------------------------------- 1. DETECT

def _dominant_collection(source: str):
    """Python: the biggest dict/list literal and whether it dominates the file
    (the data-table shape the built-in blobs). Returns (n_entries, frac)|None."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    best = 0
    total = max(1, len(source.splitlines()))
    for n in ast.walk(tree):
        elts = n.keys if isinstance(n, ast.Dict) else (n.elts if isinstance(n, ast.List) else None)
        if elts is None or len(elts) <= 10:
            continue
        span = getattr(n, "end_lineno", n.lineno) - n.lineno
        if span > best:
            best = span
            n_entries = len(elts)
    if best > 0.3 * total:
        return n_entries, round(best / total, 2)
    return None


# specialization confidence by shape: a data table is the proven, high-yield
# case; a blob or line-window fallback is plausible but weaker. Target picks the
# strongest so the ab_gate measures where specialization is most likely to win.
_SHAPE_RANK = {"table": 3, "blob": 2, "window": 1}


def _diagnose(rel: str, source: str, strat) -> tuple[str, str] | None:
    """(shape, why) for a poorly-chunked file, or None. Liberal by design — the
    ab_gate is the real arbiter, so detection only surfaces plausible cases."""
    if len(source.splitlines()) < MIN_LINES:
        return None
    try:
        r = strat.chunk_file(rel, source)
    except Exception:
        return None
    if not r.chunks:
        return None
    total_nws = sum(c.nws_chars for c in r.chunks) or 1
    biggest = max(r.chunks, key=lambda c: c.nws_chars)
    windows = [c for c in r.chunks if c.part or (c.kind in ("block", "file") and c.nws_chars > BUDGET)]
    if rel.endswith(".py"):
        dom = _dominant_collection(source)
        if dom:
            return "table", (
                f"a {dom[0]}-entry dict/list literal spans ~{int(dom[1]*100)}% of the "
                f"file; the built-in leaves it in {len(r.chunks)} coarse chunk(s), so a "
                f"query about one entry retrieves a whole blob")
    if biggest.nws_chars > BLOB_FRAC * total_nws:
        return "blob", (f"the built-in puts ~{int(100*biggest.nws_chars/total_nws)}% of this "
                        f"{r.total_lines}-line file in one chunk (a blob)")
    if windows:
        return "window", f"the built-in falls back to {len(windows)} arbitrary line-window chunk(s)"
    return None


def detect_specialization(root: Path, exclude=()) -> list[dict]:
    """Covered files the built-in chunks poorly, grouped by extension. Each
    opportunity = one ext + the target files + a diagnosis + samples."""
    root = Path(root).resolve()
    reg = build_registry(root.name)
    names = EXCLUDE_DIRS | {x for x in (*load_ignore(root), *exclude) if "/" not in x}
    by_ext: dict[str, list[dict]] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
            continue
        rel = p.relative_to(root).as_posix()
        if set(rel.split("/")) & names or "/test" in f"/{rel}" or "/spec" in f"/{rel}":
            continue
        strat = strategy_for(reg, rel)
        if strat is None:
            continue
        try:
            src = p.read_text(errors="replace")
        except OSError:
            continue
        diag = _diagnose(rel, src, strat)
        if diag:
            shape, reason = diag
            by_ext.setdefault(p.suffix, []).append(
                {"rel": rel, "shape": shape, "reason": reason,
                 "lines": len(src.splitlines())})
    out = []
    for ext, files in sorted(by_ext.items(), key=lambda kv: -len(kv[1])):
        files.sort(key=lambda f: -f["lines"])
        # target + samples = the strongest-shape files (table > blob > window):
        # the gate must measure where specialization is most likely to pay off.
        by_strength = sorted(files, key=lambda f: (-_SHAPE_RANK[f["shape"]], -f["lines"]))
        out.append({
            "ext": ext,
            "files": [f["rel"] for f in files[:MAX_OPP_FILES]],
            "count": len(files),
            "target": by_strength[0]["rel"],
            "diagnoses": {f["rel"]: f["reason"] for f in by_strength[:6]},
            "samples": [f["rel"] for f in by_strength[:3]],
        })
    return out


def lit_baseline(ext: str, repo: str = ""):
    """The literature-tuned reference the LLM must beat: the built-in chunker
    for `ext` re-budgeted to 2000 nws (the measured retrieval optimum, arxiv
    2605.04763) on library code, with test/spec/docs files delegating to the
    4000 default. Free, deterministic, repo-agnostic — so an LLM strategy that
    can't beat it adds nothing and must not install. Returns None when the
    ext's chunker can't be re-budgeted (the gate then falls back to built-in)."""
    from .indexing.strategies import builtin_strategy_for

    lit = builtin_strategy_for(ext, repo)
    fallback = builtin_strategy_for(ext, repo)
    if lit is None:
        return None
    try:                                    # re-budget the inner chunker
        c = lit._chunker
        spec = getattr(c, "spec", None)
        lit._chunker = (type(c)(spec, budget=2000, repo=repo) if spec is not None
                        else type(c)(budget=2000, repo=repo))
    except Exception:                                       # noqa: BLE001
        return None

    class _LitBaseline:
        exts = (ext,)

        def chunk_file(self, relpath, source):
            low = relpath.lower()
            if "test" in low or "spec" in low or "/docs/" in low:
                return fallback.chunk_file(relpath, source)
            return lit.chunk_file(relpath, source)

        def build_edge_ctx(self, sources, repo_name):
            return None

        def extract_edges(self, relpath, source, ctx):
            return None

    return _LitBaseline()


# ------------------------------------------------------------ gate a strategy

def gate_strategy(root, strategy, ext: str, dry_run: bool = False,
                  margin: float | None = None) -> dict:
    """Measure a HAND-WRITTEN strategy against the literature baseline and, if it
    wins the A/B (forge_eval.ab_gate), install it trust-gated. `strategy` is
    either an instantiated ChunkStrategy or the source string of one. This is
    the deterministic replacement for the removed LLM generation: the human
    writes the chunker, the machine decides whether it earns a place."""
    import time

    from .forge_eval import ab_gate
    root = Path(root).resolve()
    t0 = time.time()
    code = None
    if isinstance(strategy, str):
        code = strategy
        strat = _load(code, root.name, ext)
    else:
        strat = strategy
    base = lit_baseline(ext, root.name)
    kw = {"baseline": base}
    if margin is not None:
        kw["margin"] = margin
    gate = ab_gate(root, strat, **kw)
    report = {"root": root.as_posix(), "ext": ext,
              "baseline": "lit-2000" if base else "builtin",
              "gate": gate, "seconds": round(time.time() - t0, 2)}
    if gate.get("win") and not dry_run and code is not None:
        report["installed"] = install(root, ext, code).as_posix()
        from .indexing.indexer import index_repo
        report["index"] = index_repo(root, quiet=True)
    return report


def _load(code: str, repo: str, ext: str):
    from .indexing.strategies import instantiate_strategies
    strats = instantiate_strategies(code, repo, origin=f"<specialize {ext}>")
    return next(s for s in strats if ext in s.exts)


def render_report(report: dict) -> str:
    lines = [f"# megabrain specialize · {report['root']}"]
    if report.get("error"):
        lines.append(f"error: {report['error']}")
    g = report.get("gate", {})
    ext = report.get("ext", "?")
    where = report.get("installed") or ("(dry-run)" if g.get("win") else "not installed")
    if "pooled_candidate_iou" not in g:                     # gate short-circuited
        lines.append(f"· {ext} [rejected] — {g.get('reason', 'no gate')}")
    else:
        verdict = "WIN" if g.get("win") else "no gain (rejected)"
        nchanged = len(g.get("changed_files", []))
        lines.append(
            f"{'✓' if g.get('win') else '·'} {ext} [{verdict} vs {report.get('baseline')}] "
            f"({nchanged} file(s) changed): pooled IoU "
            f"{g['pooled_builtin_iou']}→{g['pooled_candidate_iou']} "
            f"(Δ{g['delta_iou']:+}), worst-file Δ{g['worst_file_delta_iou']:+} → {where}")
    if report.get("index"):
        ix = report["index"]
        lines.append(f"reindexed: {ix['files']} files, +{ix['new_chunks']} chunks")
    if report.get("seconds") is not None:
        lines.append(f"({report['seconds']}s)")
    return "\n".join(lines)
