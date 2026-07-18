"""Flow-cache warmup & refresh — the LLM half of the flow cache.

storage.flows owns the cache MECHANICS (write/dedupe, cosine read, verbatim
serve, sha invalidation — no LLM, importable by retrieval). This module owns
the ORCHESTRATION that fills it: discovering a repo's main workflows and
running research `ask`s so each walkthrough lands in the cache via ask's write
path. It sits in the ask layer because it drives the narrator — storage never
imports upward.

The cache itself is ON by default (see storage.flows); these two entries stay
EXPLICIT commands because they cost LLM calls: `megabrain index --warm-flows` /
`flows --warm` (warm_flows) and `flows --refresh` (refresh_stale).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..storage.flows import enabled, set_enabled
from ..storage.store import Store

log = logging.getLogger(__name__)


_TEST_SEG = ("test", "tests", "spec", "specs", "__tests__", "example", "examples",
             "benchmark", "benchmarks", "vendor", "node_modules")


def _is_side_path(relpath: str) -> bool:
    """Tests/examples/vendored code describe the repo's edges, not its main
    workflows — never seed a starter question from them."""
    return any(seg in _TEST_SEG for seg in relpath.lower().split("/")[:-1])


_NAMEABLE = ("class", "function", "async_function", "interface", "struct", "module")


def _label(docline: str, symbols: list[tuple[str, str]], relpath: str) -> str:
    """A short noun phrase naming what a file IS, for the question template.

    The module docline is the best source, but it's written as prose — take
    only its head clause. The separators matter: a docline like
    "SQLite storage: chunks, vectors, skeletons, symbols, edges, file hashes"
    is a NAME followed by an inventory, so cutting at ':' yields the concept
    ("SQLite storage") while keeping the whole line would blow any length cap
    and silently fall through to a symbol name."""
    d = docline.strip().strip('"\'')
    for cut in ("—", ":", " - ", ". "):
        if cut in d:
            d = d.split(cut, 1)[0]
    d = d.strip().rstrip(".")
    if 3 <= len(d) <= 80:
        # The label sits mid-sentence ("How does X work…"), so a plain
        # capitalized word reads better lowercased — but only if it IS plain:
        # "OpenRouter"/"SQLite"/"JSON" carry internal caps on purpose.
        head = d.split(" ", 1)[0]
        return d[0].lower() + d[1:] if head[1:].islower() else d
    # No usable docline: name the file by its primary DEFINITION. Constants are
    # skipped — "How does OUTLINE_KINDS work end to end?" names a tuple, not a
    # flow (real output before this filter).
    for name, kind in symbols:
        if kind in _NAMEABLE:
            return name
    return relpath.rsplit("/", 1)[-1].rsplit(".", 1)[0].replace("_", " ")


def central_files(root: Path, limit: int) -> list[tuple[str, str]]:
    """The repo's most central files as (relpath, label), best-effort and
    LANGUAGE-AGNOSTIC.

    Ranking is edge degree PLUS symbol density, not degree alone: the import/
    call graph only covers py/ts/js/php, so a Go or Ruby repo scores 0 on
    every file and degree-only ranking returns arbitrary noise (measured: ky 0
    edges, sinatra 1, gin 9). Symbol count is available for every indexed
    language, so it carries those repos while degree still dominates where it
    exists."""
    with Store(Path(root)) as store:
        deg: dict[str, int] = {}
        for src, dst in store.db.execute("SELECT src, dst FROM edges"):
            deg[src] = deg.get(src, 0) + 1
            deg[dst] = deg.get(dst, 0) + 1
        rows = store.db.execute("SELECT path, skeleton FROM files").fetchall()
        syms: dict[str, list[tuple[str, str]]] = {}
        for f, n, k in store.db.execute(
                "SELECT file, name, kind FROM symbols ORDER BY file, line"):
            syms.setdefault(f, []).append((n, k))
    docline = {}
    for path, skel in rows:
        docline[path] = next((ln.strip() for ln in (skel or "").splitlines()
                              if ln.strip() and not ln.startswith("#")), "")[:110]
    cands = [p for p in docline if not _is_side_path(p)] or list(docline)
    # degree is the stronger signal where it exists; symbol count keeps
    # graph-less languages ranked by substance rather than insertion order.
    ranked = sorted(cands, key=lambda f: (-(deg.get(f, 0) * 3 + len(syms.get(f, []))), f))
    return [(f, _label(docline.get(f, ""), syms.get(f, []), f)) for f in ranked[:limit]]


def derive_questions(root: Path, limit: int = 6) -> list[str]:
    """Starter questions for a repo that hasn't authored any — deterministic,
    NO LLM, so any surface can call it per request. One question per central
    file, phrased as the walkthrough `ask` is good at."""
    seen, out = set(), []
    for relpath, label in central_files(Path(root), limit * 2):
        q = f"How does {label} work end to end?"
        if q.lower() in seen:
            continue
        seen.add(q.lower())
        out.append(q)
        if len(out) >= limit:
            break
    return out


def authored_questions(root: Path) -> list[str]:
    """The repo's OWN starter questions: `<root>/.megabrainqueries`, one per
    line, `#` comments. A committed statement of "these are our main
    workflows" — it drives the studio's starter chips AND seeds the warmup,
    so writing it once both documents the repo and pre-caches its answers."""
    f = Path(root) / ".megabrainqueries"
    if not f.is_file():
        return []
    return [ln.strip() for ln in
            f.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def _research_questions(root: Path, limit: int) -> list[str]:
    """Research questions covering the system's main workflows.

    A repo that AUTHORED `.megabrainqueries` already answered this — use it
    verbatim (no planner call), so warming caches exactly the questions the
    studio offers as starter chips and every one of them then serves
    instantly. Otherwise the central files + their doclines seed an LLM
    planner (index-time LLM — allowed); fail-open to derive_questions()."""
    authored = authored_questions(Path(root))
    if authored:
        return authored[:limit]
    fallback = derive_questions(Path(root), limit)
    try:
        from .. import providers
        tree = "\n".join(f"- {f}: {label}"
                         for f, label in central_files(Path(root), max(limit * 2, 12)))
        raw = providers.chat_text(
            providers.ask_model(),
            "You are exploring an unfamiliar codebase. Based on its central "
            f"files below, write {limit} specific research questions a senior "
            "engineer would ask to understand the system's MAIN WORKFLOWS "
            "end to end (one per line, no numbering, each about ONE concrete "
            f"flow — not 'what does this repo do').\n\n{tree}",
            max_tokens=600, timeout=90)
        qs = [ln.strip("-• \t") for ln in raw.splitlines() if len(ln.strip()) > 15]
        return qs[:limit] or fallback
    except Exception:                                       # noqa: BLE001
        log.debug("flow warmup planner failed; using template questions",
                  exc_info=True)
        return fallback


def warm_flows(root: Path, limit: int = 6, ask_fn=None, quiet: bool = False) -> dict:
    """EXPLICIT warmup (`megabrain index --warm-flows` / `flows --warm`): right
    after the first indexing, discover the system's main workflows and run one
    research `ask` per question — each successful walkthrough lands in the flow
    cache via ask's write path, so the cache starts full instead of building up
    lazily from your own asks. Costs `limit` LLM calls; never runs unless
    explicitly requested. Warming implies intent, so it re-enables the cache
    even on a repo that had opted out."""
    if os.environ.get("MEGABRAIN_FLOW_CACHE") == "0":
        return {"warmed": 0, "questions": [], "skipped": "flow cache killed (env)"}
    root = Path(root)
    set_enabled(root, True)                                  # warming implies intent
    questions = _research_questions(root, limit)
    if ask_fn is None:
        from .narrator import ask as ask_fn  # noqa: PLW0127
    warmed = []
    for q in questions:
        try:
            out = ask_fn(root, q)
            ok = bool(out.get("text"))
        except Exception:                                   # noqa: BLE001
            log.debug("flow warmup ask failed for %r", q, exc_info=True)
            ok = False
        warmed.append({"question": q, "cached": ok})
        if not quiet:
            log.info("warm-flows %s %r", "✓" if ok else "✗", q[:80])
    with Store(root) as store:
        n = len(store.load_flows()[0])
    return {"warmed": sum(w["cached"] for w in warmed), "flows_total": n,
            "questions": warmed}


def refresh_stale(root, ask_fn=None, quiet: bool = False) -> dict:
    """UPDATE instead of expire: for each stale flow, re-run its ORIGINAL
    question against the current code so the cached walkthrough is regenerated
    fresh (via ask's write path). Flows whose cited files all vanished can't be
    re-asked and are dropped. Opt-in (`megabrain flows --refresh`) because it
    costs one `ask` per changed flow — the plain 60s auto-refresh only prunes."""
    if not enabled(root):
        return {"refreshed": 0, "dropped": 0, "skipped": "flow cache off"}
    root = Path(root)
    if ask_fn is None:
        from .narrator import ask as ask_fn
    with Store(root) as store:
        stale, current = store.stale_flows(), store.all_paths()
        to_reask, dropped = [], 0
        for m in stale:
            store.delete_flow(m["id"])                     # clear the stale row first
            if any(f in current for f in m["files"]):      # some source still exists
                to_reask.append(m["question"])
            else:
                dropped += 1
        store.commit()
    refreshed = 0
    for q in to_reask:
        try:
            if ask_fn(root, q).get("text"):                # re-caches via write path
                refreshed += 1
        except Exception:                                  # noqa: BLE001
            log.debug("flow refresh ask failed for %r", q, exc_info=True)
        if not quiet:
            log.info("refresh-flows ↻ %r", q[:80])
    return {"refreshed": refreshed, "dropped": dropped, "stale": len(stale)}
