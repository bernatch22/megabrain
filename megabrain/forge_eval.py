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
  2. evaluate(root, target, strategy) — index a throwaway copy of the repo with
     `target` chunked by `strategy` (everything else built-in), then for each
     probe measure, against real pplx embeddings:
         IoU   = overlap(best-overlapping retrieved chunk of the file, span)
                 / union    (a miss => 0) — punishes blobs AND fragmentation.
         hit@k = an overlapping chunk is within the top-k chunks globally.
  3. ab_gate(root, target, candidate) — evaluate built-in vs candidate; the
     candidate WINS only if it lifts mean IoU by >= IOU_MARGIN without dropping
     hit@1 (tightness that survives real ranking, not just smaller chunks).

Validated on requests/status_codes.py (a 68-entry HTTP-status dict the built-in
leaves as one blob): built-in IoU 0.009 / hit@1 0.50 -> specialized 0.098 /
0.71. Normal code files delegate byte-identically, so mean over a repo only
moves where a data-shaped file exists.
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
    from .retrieval.query import _score_chunks, load_state
    st = load_state(root)
    ious = []
    hits = {k: 0 for k in TOPK}
    for q, a, b in probes:
        metas, fused = _score_chunks(st, q)
        order = np.argsort(-fused)
        best_iou, best_rank = 0.0, None
        for rank, ci in enumerate(order):
            m = metas[ci]
            if m["file"] != target:
                continue
            s, e = m["start_line"], m["end_line"]
            inter = max(0, min(b, e) - max(a, s) + 1)
            if inter <= 0:
                continue
            iou = inter / (max(b, e) - min(a, s) + 1)
            if iou > best_iou:
                best_iou, best_rank = iou, rank
        ious.append(best_iou)
        for k in TOPK:
            if best_rank is not None and best_rank < k:
                hits[k] += 1
    n = max(1, len(probes))
    return {"mean_iou": round(sum(ious) / n, 4),
            **{f"hit@{k}": round(hits[k] / n, 4) for k in TOPK}, "n": len(probes)}


def changed_files(root: Path, candidate) -> list[str]:
    """Files whose chunk spans the candidate changes vs the built-in — exactly
    the files whose retrieval could move, so exactly what the gate must measure
    (a shape-router touches a family, not just the one target)."""
    from .indexing.strategies import builtin_strategy_for
    root = Path(root).resolve()
    names = {".git", "node_modules", "__pycache__", ".megabrain", "dist", "build"}
    out = []
    for ext in candidate.exts:
        builtin = builtin_strategy_for(ext, root.name)
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


def ab_gate(root: Path, candidate, targets=None, margin: float = IOU_MARGIN,
            regress_tol: float = 0.01) -> dict:
    """Measure the candidate against the built-in on EVERY file it changes (not
    just one target), against the real index. WIN iff pooled mean IoU lifts by
    >= margin AND no changed file regresses its own IoU by more than regress_tol
    — so a strategy that helps the data table but silently hurts a sibling file
    is rejected."""
    root = Path(root).resolve()
    files = targets if targets is not None else changed_files(root, candidate)
    if not files:
        return {"win": False, "reason": "candidate changes no files", "files": []}
    probes = {f: probe_spans(root / f) for f in files}
    probes = {f: pr for f, pr in probes.items() if pr}
    if not probes:
        return {"win": False, "reason": "no probe spans on changed files", "files": files}

    base_tmp, base_dst = _index_copy(root, None)
    cand_tmp, cand_dst = _index_copy(root, candidate)
    try:
        per_file, pooled_b, pooled_c = {}, [], []
        for f, pr in probes.items():
            b = _measure(base_dst, f, pr)
            c = _measure(cand_dst, f, pr)
            per_file[f] = {"builtin": b, "candidate": c,
                           "delta_iou": round(c["mean_iou"] - b["mean_iou"], 4)}
            pooled_b.append(b["mean_iou"] * len(pr))
            pooled_c.append(c["mean_iou"] * len(pr))
    finally:
        shutil.rmtree(base_tmp, ignore_errors=True)
        shutil.rmtree(cand_tmp, ignore_errors=True)

    npr = sum(len(pr) for pr in probes.values())
    iou_b, iou_c = round(sum(pooled_b) / npr, 4), round(sum(pooled_c) / npr, 4)
    worst = min(per_file.values(), key=lambda d: d["delta_iou"])
    win = (iou_c - iou_b >= margin and worst["delta_iou"] >= -regress_tol)
    return {"win": win, "target": max(per_file, key=lambda f: per_file[f]["delta_iou"]),
            "changed_files": list(probes), "pooled_builtin_iou": iou_b,
            "pooled_candidate_iou": iou_c, "delta_iou": round(iou_c - iou_b, 4),
            "worst_file_delta_iou": worst["delta_iou"], "per_file": per_file}
