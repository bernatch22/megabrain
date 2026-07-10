"""forge — megabrain writes its own chunkers.

`forge(root)` closes the loop that custom strategies opened (examples/02):

  1. DETECT   — census the repo for text content the active registry cannot
                index (uncovered extensions), deterministically. No LLM.
  2. GENERATE — one chat call (the same provider stack as `ask`: Claude Agent
                SDK or OpenRouter) writes a ChunkStrategy for that extension,
                from the real contract source + sample files from THIS repo.
  3. VALIDATE — the machine-checkable oracle: the candidate must chunk EVERY
                matching file in the repo with a clean `validate_partition`
                (plus compile/instantiate checks). Failures feed back into a
                repair loop (≤3 attempts). A bad chunker cannot be installed.
  4. INSTALL  — the vetted source lands in `<repo>/.megabrain/strategies/
                <ext>.py` and its sha256 is recorded in the user-level trust
                store (~/.megabrain/trust.json). From then on index_repo —
                including the 60s auto-refresh — loads it automatically
                (indexing/strategies.load_repo_strategies), so the new
                extension never falls out of the index.

The LLM writes code exactly once, at forge time, gated by the partition
oracle — the retrieval path stays LLM-free (hard rule 1) and index time runs
only vetted, trusted code. forge fails LOUD (it is an explicit user action),
unlike ask's fail-open narration.
"""

from __future__ import annotations

import inspect
import logging
import re
import time
from pathlib import Path

from .chunkers import base as chunkers_base
from .indexing.indexer import EXCLUDE_DIRS, MAX_FILE_BYTES, load_ignore
from .indexing.strategies import (
    STRATEGY_DIR,
    all_exts,
    build_registry,
    instantiate_strategies,
    trust_file,
)

log = logging.getLogger(__name__)

ATTEMPTS = 3
MAX_SAMPLE_CHARS = 4000          # per sample file shown to the model
MAX_GEN_TOKENS = 6000
MIN_FILES = 2                    # don't forge for a lone stray file

# Never worth chunking: binary or noise even when the suffix survives a text read.
SKIP_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".pdf", ".zip",
    ".gz", ".tar", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4",
    ".sqlite", ".db", ".lock", ".map", ".min.js", ".pyc", ".class", ".jar",
    ".wasm", ".svg",
})


def forge_model() -> str:
    """Code-gen model: MEGABRAIN_FORGE_MODEL, else the ask default (qwen3-coder
    on OpenRouter / haiku alias on claude) — both fine at ~100-line codegen."""
    import os

    from . import providers
    return os.environ.get("MEGABRAIN_FORGE_MODEL") or providers.ask_model()


# ---------------------------------------------------------------- 1. DETECT

def detect(root: Path, exclude=()) -> list[dict]:
    """Uncovered-extension census: every text extension in the repo that the
    active registry (built-ins + already-installed repo strategies) cannot
    index, with file count, size and sample paths. Deterministic, no LLM."""
    root = Path(root).resolve()
    covered = set(all_exts(build_registry(root.name)))
    from .indexing.strategies import load_repo_strategies
    for s in load_repo_strategies(root, root.name):
        covered.update(s.exts)

    names = EXCLUDE_DIRS | {x for x in (*load_ignore(root), *exclude) if "/" not in x}
    globs = [x for x in (*load_ignore(root), *exclude) if "/" in x]
    found: dict[str, list[Path]] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() in covered:
            continue
        ext = p.suffix.lower()
        if not ext or ext in SKIP_EXTS:
            continue
        rel = p.relative_to(root).as_posix()
        parts = set(rel.split("/"))
        if parts & names or any(rel == g or rel.startswith(g + "/") for g in globs):
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            continue
        try:                                    # binary sniff: NUL in the head
            if b"\0" in p.read_bytes()[:2048]:
                continue
        except OSError:
            continue
        found.setdefault(ext, []).append(p)

    out = []
    for ext, files in sorted(found.items(), key=lambda kv: -len(kv[1])):
        if len(files) < MIN_FILES:
            continue
        by_size = sorted(files, key=lambda p: p.stat().st_size)
        samples = [by_size[0], by_size[len(by_size) // 2], by_size[-1]]
        samples = list(dict.fromkeys(samples))          # dedupe tiny censuses
        out.append({
            "ext": ext,
            "files": len(files),
            "bytes": sum(p.stat().st_size for p in files),
            "paths": [p.relative_to(root).as_posix() for p in files],
            "samples": [p.relative_to(root).as_posix() for p in samples],
        })
    return out


# -------------------------------------------------------------- 2. GENERATE

_EXAMPLE = '''\
class SqlStrategy:
    """One chunk per run of `;`-terminated statements, merged to the budget;
    each `CREATE <kind> <name>` becomes a Symbol; headlines form the skeleton."""

    exts = (".sql",)

    def __init__(self, repo: str = ""):
        self.repo = repo
        self.budget = DEFAULT_BUDGET

    def chunk_file(self, relpath: str, source: str) -> FileResult:
        lines = source.splitlines(keepends=True)
        total = len(lines)
        crumb = f"{self.repo} > {relpath}" if self.repo else relpath
        if total == 0:
            return FileResult(relpath, [], [], "", True, 0)
        # ... unit detection, symbols, greedy merge to self.budget ...
        # every chunk: Chunk(relpath, kind, name, start, end,
        #                    "".join(lines[start-1:end]),   # VERBATIM slice
        #                    f"{crumb} > {name}").finalize()
        # last chunk MUST end at `total`; first MUST start at 1; no gaps.
        ...

    def build_edge_ctx(self, sources, repo_name):
        return None

    def extract_edges(self, relpath, source, ctx):
        return None
'''


def _prompt(ext: str, repo_name: str, samples: list[tuple[str, str]],
            feedback: str = "") -> str:
    contract = inspect.getsource(chunkers_base)
    shown = "\n\n".join(
        f"--- sample: {rel} ---\n{text[:MAX_SAMPLE_CHARS]}" for rel, text in samples)
    fb = (f"\nYOUR PREVIOUS ATTEMPT FAILED VALIDATION. Fix ALL of this:\n"
          f"{feedback}\n") if feedback else ""
    cls = ext.lstrip(".").capitalize()
    return f"""You are writing a chunking strategy for megabrain, a code-retrieval engine.
Target: `{ext}` files of the repo `{repo_name}`. Real samples are below.

THE CONTRACT (megabrain/chunkers/base.py, verbatim — this is the whole API):

```python
{contract}
```

THE PATTERN (a strategy for .sql — yours must have this exact shape):

```python
{_EXAMPLE}
```

HARD REQUIREMENTS — validation rejects any violation:
1. Chunks are an EXACT line partition: first starts at 1, each next chunk starts
   at previous end_line + 1, last ends at total_lines. Work on
   `source.splitlines(keepends=True)`; chunk text is ALWAYS the verbatim slice
   `"".join(lines[start-1:end])`. Empty file -> FileResult(relpath, [], [], "", True, 0).
2. One class named `{cls}Strategy` with `exts = ("{ext}",)`,
   `__init__(self, repo: str = "")`, and both edge hooks returning None.
3. Merge small units greedily up to DEFAULT_BUDGET non-whitespace chars (use
   `nws`); split oversized regions at natural boundaries so no chunk is huge.
4. Chunk quality IS retrieval quality: cut at the format's natural units
   (sections/tables/keys/entries), name chunks after what they contain, give
   every chunk a breadcrumb `repo > path > name`. Extract a Symbol for every
   named entity (line-accurate) and a skeleton of one headline per entity —
   the skeleton becomes the file-level embedding.
5. Imports: ONLY Python stdlib plus
   `from megabrain import Chunk, FileResult, Symbol` and
   `from megabrain.chunkers import DEFAULT_BUDGET, nws`.
   Deterministic. No I/O, no prints, no network.
6. Call `.finalize()` on every Chunk.
{fb}
SAMPLES:

{shown}

Reply with ONE ```python code block containing the complete module (a short
docstring, imports, the class). Nothing else."""


def _extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


# -------------------------------------------------------------- 3. VALIDATE

def validate_strategy(root: Path, code: str, ext: str,
                      paths: list[str]) -> tuple[bool, str, dict]:
    """The oracle. Compile + instantiate the candidate, then chunk EVERY
    matching repo file: any exception or partition violation fails it, with a
    report precise enough to drive the repair loop."""
    root = Path(root).resolve()
    try:
        strats = instantiate_strategies(code, root.name, origin=f"<candidate {ext}>")
    except Exception as e:                                  # noqa: BLE001
        return False, f"module failed to load: {type(e).__name__}: {e}", {}
    strat = next((s for s in strats if ext in s.exts), None)
    if strat is None:
        return False, f"no strategy class claiming exts=(\"{ext}\",) was found", {}

    errs: list[str] = []
    stats = {"files": 0, "chunks": 0, "symbols": 0}
    for rel in paths:
        src = (root / rel).read_text(errors="replace")
        try:
            r = strat.chunk_file(rel, src)
        except Exception as e:                              # noqa: BLE001
            errs.append(f"{rel}: chunk_file raised {type(e).__name__}: {e}")
            continue
        for v in chunkers_base.validate_partition(r):
            errs.append(f"{rel}: partition violation: {v}")
        if r.total_lines != len(src.splitlines()):
            errs.append(f"{rel}: total_lines={r.total_lines}, file has "
                        f"{len(src.splitlines())}")
        stats["files"] += 1
        stats["chunks"] += len(r.chunks)
        stats["symbols"] += len(r.symbols)
    if errs:
        return False, "\n".join(errs[:20]), stats
    return True, (f"{stats['files']} files -> {stats['chunks']} chunks, "
                  f"{stats['symbols']} symbols, partition clean"), stats


# --------------------------------------------------------------- 4. INSTALL

def install(root: Path, ext: str, code: str) -> Path:
    """Write the vetted module to `.megabrain/strategies/<ext>.py` and record
    its sha in the user trust store — from here on, index_repo (and the auto-
    refresh) load it without forge."""
    root = Path(root).resolve()
    dst = root / STRATEGY_DIR / f"{ext.lstrip('.')}.py"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(code)
    trust_file(dst)
    return dst


# ------------------------------------------------------------ orchestrator

def forge(root, ext: str | None = None, dry_run: bool = False,
          attempts: int = ATTEMPTS, model: str | None = None,
          quiet: bool = False) -> dict:
    """Detect → generate → validate (repair loop) → install → reindex.
    `ext` limits the run to one extension; `dry_run` stops before install."""
    from . import providers

    root = Path(root).resolve()
    t0 = time.time()
    cands = detect(root)
    if ext:
        want = ext if ext.startswith(".") else f".{ext}"
        cands = [c for c in cands if c["ext"] == want]
        if not cands:
            return {"root": root.as_posix(), "candidates": [],
                    "error": f"no uncovered files with extension {want}"}
    report: dict = {"root": root.as_posix(), "candidates": cands, "forged": []}
    mdl = model or forge_model()

    for c in cands:
        samples = [(rel, (root / rel).read_text(errors="replace"))
                   for rel in c["samples"]]
        feedback, entry = "", {"ext": c["ext"], "files": c["files"],
                               "attempts": 0, "ok": False}
        for i in range(attempts):
            entry["attempts"] = i + 1
            if not quiet:
                log.info("forge %s: attempt %d/%d (%s)", c["ext"], i + 1, attempts, mdl)
            raw = providers.chat_text(mdl, _prompt(c["ext"], root.name, samples,
                                                   feedback),
                                      max_tokens=MAX_GEN_TOKENS, timeout=180)
            code = _extract_code(raw)
            ok, msg, stats = validate_strategy(root, code, c["ext"], c["paths"])
            if ok:
                entry.update(ok=True, validation=msg, stats=stats)
                if not dry_run:
                    entry["installed"] = install(root, c["ext"], code).as_posix()
                else:
                    entry["code"] = code
                break
            feedback = msg
            entry["validation"] = msg
        report["forged"].append(entry)

    if not dry_run and any(e["ok"] for e in report["forged"]):
        from .indexing.indexer import index_repo
        report["index"] = index_repo(root, quiet=True)
    report["seconds"] = round(time.time() - t0, 2)
    return report


def render_report(report: dict) -> str:
    """Human-readable forge report for the CLI and the MCP tool."""
    lines = [f"# megabrain forge · {report['root']}"]
    if report.get("error"):
        lines.append(f"error: {report['error']}")
    if not report["candidates"]:
        lines.append("no uncovered text extensions found — the index already "
                     "sees everything it can.")
    for c in report["candidates"]:
        lines.append(f"- uncovered {c['ext']}: {c['files']} files, {c['bytes']} bytes "
                     f"(samples: {', '.join(c['samples'])})")
    for e in report.get("forged", []):
        if e["ok"]:
            where = e.get("installed") or "(dry-run, not installed)"
            lines.append(f"✓ {e['ext']} strategy forged in {e['attempts']} attempt(s) — "
                         f"{e['validation']} → {where}")
        else:
            lines.append(f"✗ {e['ext']} failed after {e['attempts']} attempt(s): "
                         f"{e.get('validation', 'no attempt ran')[:400]}")
    if report.get("index"):
        ix = report["index"]
        lines.append(f"reindexed: {ix['files']} files, +{ix['new_chunks']} chunks, "
                     f"{ix['partition_violations']} violations")
    if report.get("seconds") is not None:
        lines.append(f"({report['seconds']}s)")
    return "\n".join(lines)
