"""forge --specialize — chunkers tuned to a repo's own conventions.

Coverage forge (forge.py) teaches megabrain file types it can't read at all.
Specialization is the other half of the idea: for file types it ALREADY reads,
generate a chunker that fits how THIS repo is written — where the generic
built-in chunks poorly.

The proven case (measured, forge_eval.py): a module that is one big data table
(a 68-entry HTTP-status dict) is left by the built-in as a single blob, so a
query about one entry retrieves the whole file — mean span-IoU 0.009, hit@1
0.50. A per-entry-group chunker lifts that to IoU 0.098 / hit@1 0.71, while
every NORMAL file in the repo is chunked byte-identically. That asymmetry is
the design:

  SHAPE-ROUTER — the generated strategy handles the special shape and DELEGATES
  everything else to the engine's own chunker (builtin_strategy_for). So normal
  files never change; only the data-shaped ones do. (Same pattern as the
  built-in legacy-vs-modern PHP router.)

Two gates, not one:
  1. validate_partition over EVERY matching file (the coverage oracle) — a hard,
     free gate; drives a repair loop.
  2. ab_gate (forge_eval) — index built-in vs candidate, measure retrieval on
     neutral probes; INSTALL ONLY IF the candidate lifts span-IoU without
     dropping hit@1. This is what makes specialization safe: a legal-but-worse
     chunker is measured to be worse and rejected.

Generation fans out (one LLM per extension-opportunity, in parallel), matching
"LLMs navigating the repo and summarizing a strategy".
"""

from __future__ import annotations

import ast
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .forge import (
    MAX_GEN_TOKENS,
    MAX_SAMPLE_CHARS,
    _extract_code,
    forge_model,
    install,
    validate_strategy,
)
from .indexing.indexer import EXCLUDE_DIRS, MAX_FILE_BYTES, load_ignore
from .indexing.strategies import build_registry, strategy_for

log = logging.getLogger(__name__)

ATTEMPTS = 3
BUDGET = 4000
BLOB_FRAC = 0.55           # largest chunk this share of a file's chars = a blob
MIN_LINES = 120            # only large files are worth specializing
MAX_OPP_FILES = 40         # cap files shown/validated per opportunity


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


# -------------------------------------------------------------- 2. GENERATE

def _prompt(ext: str, repo: str, opp: dict, samples: list[tuple[str, str]],
            feedback: str = "") -> str:
    import inspect

    from .chunkers import base as chunkers_base
    contract = inspect.getsource(chunkers_base)
    diag = "\n".join(f"  - {rel}: {why}" for rel, why in opp["diagnoses"].items())
    shown = "\n\n".join(f"--- {rel} ---\n{txt[:MAX_SAMPLE_CHARS]}" for rel, txt in samples)
    fb = f"\nYOUR PREVIOUS ATTEMPT FAILED. Fix ALL of this:\n{feedback}\n" if feedback else ""
    cls = ext.lstrip(".").capitalize()
    return f"""You are writing a SPECIALIZATION chunking strategy for megabrain (a code-
retrieval engine) for `{ext}` files in the repo `{repo}`.

THE DATA MODEL (megabrain/chunkers/base.py, verbatim — use these exact fields;
`Chunk(file, kind, name, start_line, end_line, text, breadcrumb)`):

```python
{contract}
```

The built-in `{ext}` chunker works fine for normal files, but chunks these
poorly:
{diag}

Write a SHAPE-ROUTER strategy: detect the special shape and split it into tight,
semantically-named chunks; for every OTHER file, delegate to the built-in
chunker unchanged. This is critical — you must NOT change how normal files are
chunked.

REQUIRED STRUCTURE:

```python
from megabrain import Chunk, FileResult, Symbol
from megabrain.chunkers import DEFAULT_BUDGET, nws
from megabrain.indexing.strategies import builtin_strategy_for


class {cls}SpecialStrategy:
    exts = ("{ext}",)

    def __init__(self, repo: str = ""):
        self.repo = repo
        self._fallback = builtin_strategy_for("{ext}", repo)  # the built-in chunker

    def chunk_file(self, relpath: str, source: str) -> FileResult:
        if not self._is_special(source):
            return self._fallback.chunk_file(relpath, source)   # delegate — unchanged
        lines = source.splitlines(keepends=True)
        total = len(lines)
        if total == 0:
            return FileResult(relpath, [], [], "", True, 0)
        # PARTITION BY CONSTRUCTION — do NOT compute end_lines by hand.
        # 1. compute `cuts`: a sorted list of chunk START lines, always [1, ...]
        cuts = self._cut_lines(lines)            # e.g. [1, 24, 61, 118]
        # 2. derive the chunks mechanically (this can never gap or overlap):
        bounds = list(zip(cuts, [c - 1 for c in cuts[1:]] + [total]))
        # 3. one Chunk per (s, e) in bounds, text = "".join(lines[s-1:e])

    def _is_special(self, source: str) -> bool:
        ...   # cheap, precise detector for the shape diagnosed above

    def build_edge_ctx(self, sources, repo_name):
        return None

    def extract_edges(self, relpath, source, ctx):
        return None
```

HARD REQUIREMENTS — validation rejects any violation:
1. EXACT line partition on the special path. Build chunks ONLY via the cut-lines
   pattern shown above (a sorted list of start lines beginning with 1, chunks
   derived mechanically) — never compute end_lines independently, that is how
   gaps happen. Chunk text is ALWAYS the verbatim slice
   `"".join(lines[s-1:e])`. Empty file -> FileResult(relpath, [], [], "", True, 0).
2. For the data-table shape: emit ~one chunk per SMALL GROUP of entries — aim
   for ~300-600 non-whitespace chars per chunk via `nws` (roughly 3-8 entries),
   NOT the full 4000 budget: tight chunks are the whole point, they are what
   lifts span precision. Plus a header chunk (text before the table) and a tail
   chunk (after it) if present. Name each chunk after what it contains;
   breadcrumb `repo > path > name`. One Symbol per named entry.
3. `_is_special` MUST be precise: it returns False for the normal files so they
   delegate. When in doubt, delegate.
4. Only stdlib + the three imports shown. Deterministic. `.finalize()` every Chunk.
{fb}
SAMPLE FILES (the shape to specialize):

{shown}

Reply with ONE ```python code block: the complete module. Nothing else."""


def _ext_files(root: Path, ext: str, exclude=()) -> list[str]:
    """EVERY file of `ext` in the repo — a shape-router runs on all of them
    (delegating most), so partition must be validated over all, not just the
    poorly-chunked subset. (The subset-only gap once let a buggy constructor
    pass validation and crash at index time.)"""
    names = EXCLUDE_DIRS | {x for x in (*load_ignore(root), *exclude) if "/" not in x}
    out = []
    for p in sorted(root.rglob(f"*{ext}")):
        if not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
            continue
        rel = p.relative_to(root).as_posix()
        if set(rel.split("/")) & names:
            continue
        out.append(rel)
    return out


def _generate_one(root: Path, opp: dict, repo: str, mdl: str,
                  attempts: int, feedback: str = "") -> dict:
    """One generation round: LLM → partition repair loop. `feedback` seeds the
    first prompt (used by the gate-feedback round in specialize())."""
    from . import providers
    ext = opp["ext"]
    samples = [(rel, (root / rel).read_text(errors="replace")) for rel in opp["samples"]]
    all_files = _ext_files(root, ext)          # validate over ALL ext files
    entry = {"ext": ext, "count": opp["count"], "target": opp["target"],
             "attempts": 0, "ok": False, "code": None}
    for i in range(attempts):
        entry["attempts"] = i + 1
        log.info("specialize %s: attempt %d/%d (%s)", ext, i + 1, attempts, mdl)
        raw = providers.chat_text(mdl, _prompt(ext, repo, opp, samples, feedback),
                                  max_tokens=MAX_GEN_TOKENS, timeout=180)
        code = _extract_code(raw)
        ok, msg, _ = validate_strategy(root, code, ext, all_files)
        entry["validation"] = msg
        if ok:
            entry.update(ok=True, code=code)
            return entry
        feedback = msg
    return entry


# ------------------------------------------------------------ orchestrator

def specialize(root, ext: str | None = None, dry_run: bool = False,
               attempts: int = ATTEMPTS, model: str | None = None,
               margin: float | None = None, quiet: bool = False) -> dict:
    """Detect poorly-chunked covered files → generate shape-routers in parallel
    → partition-validate → A/B gate (forge_eval) → install only the winners."""
    import time

    from .forge_eval import ab_gate
    root = Path(root).resolve()
    t0 = time.time()
    opps = detect_specialization(root)
    if ext:
        want = ext if ext.startswith(".") else f".{ext}"
        opps = [o for o in opps if o["ext"] == want]
        if not opps:
            return {"root": root.as_posix(), "opportunities": [],
                    "error": f"no specialization opportunity for {want}"}
    report = {"root": root.as_posix(), "opportunities": opps, "specialized": []}
    mdl = model or forge_model()

    # GENERATE in parallel — one LLM per extension-opportunity
    with ThreadPoolExecutor(max_workers=min(4, len(opps) or 1)) as pool:
        gens = list(pool.map(lambda o: _generate_one(root, o, root.name, mdl, attempts), opps))

    # GATE + install sequentially (each gate re-indexes; keep it serial). A
    # partition-valid candidate that LOSES the gate gets ONE regeneration with
    # the measured result as feedback — the metric closes the loop.
    kw = {} if margin is None else {"margin": margin}
    for opp, gen in zip(opps, gens):
        e = {k: gen[k] for k in ("ext", "count", "target", "attempts", "ok", "validation")}
        if not gen["ok"]:
            report["specialized"].append(e)
            continue
        gate = ab_gate(root, _load(gen["code"], root.name, gen["ext"]), **kw)
        if not gate["win"] and "delta_iou" in gate:
            fb = (f"Your previous strategy was VALID but the retrieval gate rejected "
                  f"it: pooled span-IoU improved only {gate['delta_iou']:+} (needs "
                  f">= {margin if margin is not None else 'the margin'}). Your chunks "
                  f"were too coarse. Split MUCH tighter: one chunk per ~3-8 entries "
                  f"(~300-600 non-whitespace chars), each named after the entries it "
                  f"contains — tight chunks are what lifts span precision.")
            regen = _generate_one(root, opp, root.name, mdl, attempts, feedback=fb)
            if regen["ok"]:
                gate2 = ab_gate(root, _load(regen["code"], root.name, regen["ext"]), **kw)
                if gate2.get("win"):
                    gen, gate = regen, gate2
                    e.update(attempts=gen["attempts"] + attempts,
                             validation=gen["validation"])
        e["gate"] = gate
        if gate["win"] and not dry_run:
            e["installed"] = install(root, gen["ext"], gen["code"]).as_posix()
        elif gate["win"]:
            e["code"] = gen["code"]           # dry-run: would install
        report["specialized"].append(e)

    if not dry_run and any(e.get("installed") for e in report["specialized"]):
        from .indexing.indexer import index_repo
        report["index"] = index_repo(root, quiet=True)
    report["seconds"] = round(time.time() - t0, 2)
    return report


def _load(code: str, repo: str, ext: str):
    from .indexing.strategies import instantiate_strategies
    strats = instantiate_strategies(code, repo, origin=f"<specialize {ext}>")
    return next(s for s in strats if ext in s.exts)


def render_report(report: dict) -> str:
    lines = [f"# megabrain forge --specialize · {report['root']}"]
    if report.get("error"):
        lines.append(f"error: {report['error']}")
    if not report.get("opportunities"):
        lines.append("no specialization opportunities — the built-in chunkers fit "
                     "this repo.")
    for o in report["opportunities"]:
        lines.append(f"- {o['ext']}: {o['count']} poorly-chunked file(s), "
                     f"target {o['target']}")
    for e in report.get("specialized", []):
        if not e["ok"]:
            lines.append(f"✗ {e['ext']}: generation failed after {e['attempts']} "
                         f"attempt(s) — {e.get('validation', '')[:200]}")
            continue
        g = e.get("gate", {})
        verdict = "WIN" if g.get("win") else "no gain (rejected)"
        where = e.get("installed") or ("(dry-run)" if g.get("win") else "not installed")
        if "pooled_candidate_iou" not in g:                  # gate short-circuited
            lines.append(f"· {e['ext']} [rejected] — {g.get('reason', 'no gate')}")
            continue
        nchanged = len(g.get("changed_files", []))
        lines.append(
            f"{'✓' if g.get('win') else '·'} {e['ext']} [{verdict}] "
            f"({nchanged} file(s) changed): pooled IoU "
            f"{g['pooled_builtin_iou']}→{g['pooled_candidate_iou']} "
            f"(Δ{g['delta_iou']:+}), worst-file Δ{g['worst_file_delta_iou']:+} → {where}")
    if report.get("index"):
        ix = report["index"]
        lines.append(f"reindexed: {ix['files']} files, +{ix['new_chunks']} chunks")
    if report.get("seconds") is not None:
        lines.append(f"({report['seconds']}s)")
    return "\n".join(lines)
