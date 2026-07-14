"""Flow-cache warmup & refresh — the LLM half of the flow cache.

storage.flows owns the cache MECHANICS (write/dedupe, cosine read, verbatim
serve, sha invalidation — no LLM, importable by retrieval). This module owns
the ORCHESTRATION that fills it: discovering a repo's main workflows and
running research `ask`s so each walkthrough lands in the cache via ask's write
path. It sits in the ask layer because it drives the narrator — storage never
imports upward.

Both entries are OPT-IN and cost LLM calls: `megabrain index --warm-flows` /
`flows --warm` (warm_flows) and `flows --refresh` (refresh_stale).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..storage.flows import enabled, set_enabled
from ..storage.store import Store

log = logging.getLogger(__name__)


def _research_questions(root: Path, limit: int) -> list[str]:
    """Research questions covering the system's main workflows, derived from
    the fresh index: the graph's hub files (highest edge degree) + their
    doclines seed an LLM planner (index-time LLM — allowed); fail-open to
    deterministic template questions from the same hubs."""
    with Store(Path(root)) as store:
        deg: dict[str, int] = {}
        for src, dst in store.db.execute("SELECT src, dst FROM edges"):
            deg[src] = deg.get(src, 0) + 1
            deg[dst] = deg.get(dst, 0) + 1
        rows = store.db.execute("SELECT path, skeleton FROM files").fetchall()
    docline = {}
    for path, skel in rows:
        first = next((ln.strip() for ln in (skel or "").splitlines()
                      if ln.strip() and not ln.startswith("#")), "")
        docline[path] = first[:110]
    hubs = sorted(docline, key=lambda f: -deg.get(f, 0))[:max(limit * 2, 12)]
    fallback = [f"how does {docline[f] or f} work end to end"
                for f in hubs if docline.get(f) or f][:limit]
    try:
        from .. import providers
        tree = "\n".join(f"- {f}: {docline.get(f, '')}" for f in hubs)
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
    """OPT-IN warmup (`megabrain index --warm-flows` / `flows --warm`): right
    after the first indexing, discover the system's main workflows and run one
    research `ask` per question — each successful walkthrough lands in the flow
    cache via ask's write path, so the cache starts full instead of building up
    lazily. Costs `limit` LLM calls; never runs unless explicitly requested.
    Warming implies intent, so it TURNS THE MODE ON for this repo."""
    if os.environ.get("MEGABRAIN_FLOW_CACHE") == "0":
        return {"warmed": 0, "questions": [], "skipped": "flow cache killed (env)"}
    root = Path(root)
    set_enabled(root, True)                                  # opt-in by warming
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
