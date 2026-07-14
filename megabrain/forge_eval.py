"""forge_eval — the self-supervised quality gate for specialization.

Coverage forge (forge.py v1) is gated by one oracle: `validate_partition`. For
an UNCOVERED extension that suffices — any legal chunker beats not indexing the
file. But SPECIALIZATION rewrites how an ALREADY-covered file is chunked, and a
legal chunker can still be *worse* than the built-in. So specialization needs a
second, empirical gate: does retrieval actually improve?

This module measures that with no human labels, against the REAL index:

  1. probe_spans(path)  — neutral ground-truth sub-structures of the file
     (python dict-entries / defs via ast; generic blank-line blocks otherwise),
     each a (query, span) pair. Independent of any chunker, so both strategies
     are scored on the SAME targets — no bias toward either.
  2. evaluate / _measure — index a throwaway copy of the repo with the file
     chunked by the strategy (everything else built-in), then per probe,
     against real embeddings:
         IoU   = overlap(true span, the file's TOP-RANKED chunk) / union —
                 what a user actually gets when the file is retrieved. NOT
                 best-IoU-over-all-chunks (that measures geometry, not
                 retrieval, and micro-chunking games it).
         hit@k = an overlapping chunk is within the top-k chunks GLOBALLY.
  3. ab_gate(root, candidate) — built-in vs candidate on every file the
     candidate changes. WIN needs the pooled IoU lift AND hit@1 held AND no
     per-file regression AND no micro-chunking (median chunk >= 100 nws).

The gate earned its teeth empirically: an LLM candidate once scored pooled
IoU 0.55 on express with median 1-LINE chunks — perfect geometry, useless
embeddings. Rank-aware IoU + the granularity floor make that family of
metric-gaming un-installable. Normal code files delegate byte-identically,
so a repo only moves where a special-shaped file exists.
"""

from __future__ import annotations

import ast
import shutil
import tempfile
from pathlib import Path

import numpy as np

IOU_MARGIN = 0.01          # min absolute mean-IoU lift for a specialization win
MAX_PROBES = 60            # cap embed cost per gate evaluation
TOPK = (1, 5)


# ------------------------------------------------------- neutral probe targets

def _py_probes(source: str) -> list[tuple[str, int, int]]:
    """Python ground truth: the entries of the largest dict/list literal (data
    tables — where the built-in blobs), else top-level defs. ast line spans are
    a fact about the file, not about any chunker."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    # largest collection literal
    best = None
    for n in ast.walk(tree):
        elts = n.keys if isinstance(n, ast.Dict) else (n.elts if isinstance(n, ast.List) else None)
        if elts is None or len(elts) <= 10:
            continue
        span = getattr(n, "end_lineno", n.lineno) - n.lineno
        if best is None or span > best[0]:
            best = (span, n, elts)
    out: list[tuple[str, int, int]] = []
    if best and best[0] > 0.3 * len(source.splitlines()):
        _, node, elts = best
        vals = node.values if isinstance(node, ast.Dict) else node.elts
        for k, v in zip(elts, vals):
            a, b = k.lineno, getattr(v, "end_lineno", v.lineno)
            names = [e.value for e in getattr(v, "elts", []) if isinstance(e, ast.Constant)
                     and isinstance(e.value, str)]
            key = k.value if isinstance(k, ast.Constant) else ast.get_source_segment(source, k) or "?"
            q = f"{key}: " + ", ".join(names[:5]) if names else str(key)
            out.append((q.strip()[:120], a, b))
        return out
    # else: top-level defs
    for n in getattr(tree, "body", []):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(n) or ""
            q = f"{n.name} {doc}".strip()[:120]
            out.append((q, n.lineno, getattr(n, "end_lineno", n.lineno)))
    return out


def _generic_probes(source: str) -> list[tuple[str, int, int]]:
    """Language-neutral fallback: blank-line-separated blocks, queried by their
    most content-ful line. Good enough to score tightness for any text file."""
    lines = source.splitlines()
    out, start = [], None
    for i, ln in enumerate(lines, 1):
        if ln.strip():
            start = start or i
        elif start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(lines)))
    probes = []
    for a, b in out:
        if b - a < 1:
            continue
        block = lines[a - 1:b]
        salient = max(block, key=lambda s: len(s.strip()))
        probes.append((salient.strip()[:120], a, b))
    return probes


def probe_spans(path: Path) -> list[tuple[str, int, int]]:
    source = Path(path).read_text(errors="replace")
    probes = _py_probes(source) if str(path).endswith(".py") else []
    if not probes:
        probes = _generic_probes(source)
    if len(probes) > MAX_PROBES:                     # even stride subsample
        step = len(probes) / MAX_PROBES
        probes = [probes[int(i * step)] for i in range(MAX_PROBES)]
    return probes


# --------------------------------------------------------------- measurement

def _measure(root: Path, target: str, probes) -> dict:
    """Retrieval-real scoring. For each probe:

    - mean_iou = IoU(true span, the target file's TOP-RANKED chunk) — what a
      user actually gets when this file is retrieved for that query. This is
      deliberately NOT best-IoU-over-all-chunks: that variant measures chunk
      geometry, not retrieval, and micro-chunking games it (a 1-line chunk
      always exists that matches any span; it scored 0.55 pooled IoU on express
      while embedding as noise).
    - hit@k = an overlapping chunk sits within the top-k GLOBALLY (rank across
      the whole repo, all files competing)."""
    from .retrieval.scoring import score_chunks
    from .retrieval.state import load_state
    st = load_state(root)
    ious = []
    hits = {k: 0 for k in TOPK}
    for q, a, b in probes:
        metas, fused = score_chunks(st, q)
        order = np.argsort(-fused)
        top_iou, overlap_rank = None, None
        for rank, ci in enumerate(order):
            m = metas[ci]
            if m.file != target:
                continue
            s, e = m.start_line, m.end_line
            inter = max(0, min(b, e) - max(a, s) + 1)
            iou = inter / (max(b, e) - min(a, s) + 1) if inter > 0 else 0.0
            if top_iou is None:                 # the file's best-ranked chunk
                top_iou = iou
            if inter > 0 and overlap_rank is None:
                overlap_rank = rank
            if top_iou is not None and overlap_rank is not None:
                break
        ious.append(top_iou or 0.0)
        for k in TOPK:
            if overlap_rank is not None and overlap_rank < k:
                hits[k] += 1
    n = max(1, len(probes))
    return {"mean_iou": round(sum(ious) / n, 4),
            **{f"hit@{k}": round(hits[k] / n, 4) for k in TOPK}, "n": len(probes)}


def changed_files(root: Path, candidate, baseline=None) -> list[str]:
    """Files whose chunk spans the candidate changes vs the reference chunking
    (`baseline` strategy, default the built-in) — exactly the files whose
    retrieval could move, so exactly what the gate must measure (a shape-router
    touches a family, not just the one target)."""
    from .indexing.strategies import builtin_strategy_for
    root = Path(root).resolve()
    names = {".git", "node_modules", "__pycache__", ".megabrain", "dist", "build"}
    out = []
    for ext in candidate.exts:
        builtin = baseline or builtin_strategy_for(ext, root.name)
        for p in sorted(root.rglob(f"*{ext}")):
            if not p.is_file() or set(p.relative_to(root).parts) & names:
                continue
            rel = p.relative_to(root).as_posix()
            try:
                src = p.read_text(errors="replace")
                a = [(c.start_line, c.end_line) for c in candidate.chunk_file(rel, src).chunks]
                b = [(c.start_line, c.end_line) for c in builtin.chunk_file(rel, src).chunks]
            except Exception:
                continue
            if a != b:
                out.append(rel)
    return out


def _index_copy(root: Path, strategy):
    from .indexing.indexer import index_repo
    tmp = Path(tempfile.mkdtemp(prefix="mb-forge-eval-"))
    dst = tmp / root.name
    shutil.copytree(root, dst, ignore=shutil.ignore_patterns(
        ".megabrain", ".git", "node_modules", "__pycache__"))
    index_repo(dst, quiet=True, strategies=[strategy] if strategy else [])
    return tmp, dst


MIN_MEDIAN_NWS = 100       # degenerate-granularity floor for changed files


def _granularity_violation(root: Path, candidate, files: list[str]) -> str | None:
    """Reject micro-chunking outright: 1-line chunks can win span geometry
    while embedding as noise. A file violates when the candidate's median
    chunk is under MIN_MEDIAN_NWS non-whitespace chars AND substantially finer
    than the BUILT-IN's median on the same file — a small file whose chunks
    are naturally small is not the candidate's doing and must not veto the
    whole strategy. Checked before any indexing (cheap)."""
    from .chunkers.base import nws
    from .indexing.strategies import builtin_strategy_for

    def median_nws(strat, f, src):
        r = strat.chunk_file(f, src)
        if not r.chunks:
            return None
        sizes = sorted(nws(c.text) for c in r.chunks)
        return sizes[len(sizes) // 2]

    builtins = {ext: builtin_strategy_for(ext, root.name) for ext in candidate.exts}
    for f in files:
        src = (root / f).read_text(errors="replace")
        try:
            med = median_nws(candidate, f, src)
        except Exception as e:                              # noqa: BLE001
            return f"{f}: chunk_file raised {type(e).__name__}: {e}"
        if med is None or med >= MIN_MEDIAN_NWS:
            continue
        ext = "." + f.rsplit(".", 1)[-1]
        base = builtins.get(ext)
        base_med = median_nws(base, f, src) if base else None
        if base_med and med < 0.5 * base_med:
            return (f"{f}: median chunk is {med} non-whitespace chars "
                    f"(< {MIN_MEDIAN_NWS}, and <50% of the built-in's "
                    f"{base_med}) — chunks this small embed poorly; group "
                    f"more content per chunk")
    return None


def ab_gate(root: Path, candidate, targets=None, margin: float = IOU_MARGIN,
            regress_tol: float = 0.01, baseline=None) -> dict:
    """Measure the candidate against a reference chunking on EVERY file it
    changes (not just one target), against the real index. The reference is the
    built-in by default; pass `baseline` (e.g. the literature-tuned budget-2000
    strategy) to raise the bar — a candidate that can't beat the free recipe
    adds nothing and must not install. WIN requires ALL of:

      1. pooled top-chunk IoU lifts by >= margin,
      2. pooled hit@1 does not regress (the tight chunks must still WIN the
         global ranking — geometry alone is not retrieval),
      3. no changed file regresses its own IoU by more than regress_tol,
      4. no changed file is micro-chunked (median >= MIN_MEDIAN_NWS nws).

    2 and 4 exist because span-IoU alone is gameable by 1-line chunks."""
    root = Path(root).resolve()
    files = targets if targets is not None else changed_files(root, candidate, baseline)
    if not files:
        return {"win": False, "reason": "candidate changes no files", "files": []}
    gran = _granularity_violation(root, candidate, files)
    if gran:
        return {"win": False, "reason": f"degenerate granularity: {gran}",
                "changed_files": files}
    probes = {f: probe_spans(root / f) for f in files}
    probes = {f: pr for f, pr in probes.items() if pr}
    if not probes:
        return {"win": False, "reason": "no probe spans on changed files", "files": files}

    base_tmp, base_dst = _index_copy(root, baseline)
    cand_tmp, cand_dst = _index_copy(root, candidate)
    try:
        per_file, pooled_b, pooled_c, hit_b, hit_c = {}, [], [], [], []
        for f, pr in probes.items():
            b = _measure(base_dst, f, pr)
            c = _measure(cand_dst, f, pr)
            per_file[f] = {"builtin": b, "candidate": c,
                           "delta_iou": round(c["mean_iou"] - b["mean_iou"], 4)}
            pooled_b.append(b["mean_iou"] * len(pr))
            pooled_c.append(c["mean_iou"] * len(pr))
            hit_b.append(b["hit@1"] * len(pr))
            hit_c.append(c["hit@1"] * len(pr))
    finally:
        shutil.rmtree(base_tmp, ignore_errors=True)
        shutil.rmtree(cand_tmp, ignore_errors=True)

    npr = sum(len(pr) for pr in probes.values())
    iou_b, iou_c = round(sum(pooled_b) / npr, 4), round(sum(pooled_c) / npr, 4)
    h1_b, h1_c = round(sum(hit_b) / npr, 4), round(sum(hit_c) / npr, 4)
    worst = min(per_file.values(), key=lambda d: d["delta_iou"])
    win = (iou_c - iou_b >= margin
           and h1_c >= h1_b - 1e-9
           and worst["delta_iou"] >= -regress_tol)
    return {"win": win, "target": max(per_file, key=lambda f: per_file[f]["delta_iou"]),
            "changed_files": list(probes), "pooled_builtin_iou": iou_b,
            "pooled_candidate_iou": iou_c, "delta_iou": round(iou_c - iou_b, 4),
            "pooled_builtin_hit1": h1_b, "pooled_candidate_hit1": h1_c,
            "worst_file_delta_iou": worst["delta_iou"], "per_file": per_file}
