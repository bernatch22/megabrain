"""flows — self-caching workflow retrieval (OPT-IN, off by default).

This is a MODE the developer turns on per repo (`megabrain flows --enable`, or
implied by `--warm-flows`); the env var MEGABRAIN_FLOW_CACHE forces it on/off
globally. When OFF — the default — nothing here runs: `query`/`ask` behave
exactly as they did before, at zero cost. When ON:

Every `ask` synthesizes a cross-file walkthrough (a WORKFLOW: "VAD detects
speech → TurnController.on_vad_start → cancel TTS"). That synthesis is
expensive knowledge the engine used to throw away. This module caches it in
the index so the NEXT related question retrieves the whole flow at once:

  WRITE (ask time — LLM + one embed call, both allowed off the query path):
      after a successful spliced answer, the prose (citation markers stripped)
      is embedded together with the question and stored in the `flows` table
      with {cited file: sha}. Near-duplicate flows (cos > 0.92) replace the
      old entry instead of piling up. Fail-open: a cache error never breaks ask.

  READ (query time — cosine only, hard rule 1 intact):
      search_with_state scores the query vector against the flow matrix and
      ATTACHES matching flows to the result. Flows never rank or displace
      files (the rule-3 analog); their source files are added to the tail of
      RELATED only when missing — pure additions, so bundle_full can only go
      up. `ask` feeds matched flow text to the narrator as non-citable context.

  INVALIDATION (index time):
      a flow records the sha of every file it cites; index_repo prunes flows
      whose files changed or vanished, so a stale walkthrough can never
      outlive the code it describes. (`ask` still splices real code from disk
      regardless — a stale flow could only ever mis-prioritize, not fabricate.)

Validated in prototype: a barge-in flow cached from one question was retrieved
by a fully re-worded paraphrase ("how does the system stop the bot from talking
when someone starts speaking over it"). Related: Knowledge Compression via
Question Generation (arxiv 2506.13778) — indexing synthesized knowledge lifts
multi-hop retrieval.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

import numpy as np

from .store import Store

log = logging.getLogger(__name__)

FLOW_MIN_SIM = 0.62      # normalized (cos+1)/2 floor to attach a flow
FLOW_TOP_K = 2           # at most this many flows per query
FLOW_DEDUP_SIM = 0.92    # raw-vector cosine above this = same flow, replace
FLOW_TEXT_CAP = 4000     # chars of prose stored/rendered per flow
FLOW_FILE_ADDS = 3       # max missing flow-source files appended to RELATED

_CITE = re.compile(r"\[\[[^\]]*\]\]")
_META = "flow_cache"


def enabled(root=None) -> bool:
    """OFF by default — the flow cache is an opt-in MODE. Plain `query`/`ask`
    behave exactly as before unless a dev turns it on for a repo (persisted in
    the index meta via set_enabled / `megabrain flows --enable`, or implied by
    `--warm-flows`). The env var overrides both ways as a global switch/kill:
    MEGABRAIN_FLOW_CACHE=1 forces on, =0 forces off (kill wins over the flag)."""
    env = os.environ.get("MEGABRAIN_FLOW_CACHE")
    if env is not None:
        return env != "0"
    if root is None:
        return False
    try:
        with Store(Path(root)) as store:
            return bool(store.get_meta(_META))
    except Exception:                                       # noqa: BLE001
        return False


def set_enabled(root, on: bool) -> None:
    with Store(Path(root)) as store:
        store.set_meta(_META, bool(on))
        store.commit()


def cache_flow(root: Path, question: str, text: str, cited_files: list[str],
               emb=None) -> int | None:
    """Persist one ask synthesis as a retrievable flow. Returns the flow id,
    or None when skipped (mode off, nothing cited, or any error — fail-open)."""
    if not enabled(root) or not cited_files or not text.strip():
        return None
    try:
        if emb is None:
            from .providers.embeddings import Embedder
            emb = Embedder()
        prose = _CITE.sub("", text).strip()[:FLOW_TEXT_CAP]
        with Store(Path(root)) as store:
            files = {}
            for f in sorted(set(cited_files)):
                p = Path(root) / f
                if p.is_file():
                    files[f] = hashlib.sha256(
                        p.read_text(errors="replace").encode()).hexdigest()
            if not files:
                return None
            vec = emb.embed([f"{question}\n\n{prose}"])[0].astype(np.float32)
            # near-duplicate → replace (keep the freshest synthesis)
            metas, M = store.load_flows()
            if len(metas):
                sims = M @ vec / (np.linalg.norm(M, axis=1) * np.linalg.norm(vec) + 1e-9)
                for i in np.where(sims > FLOW_DEDUP_SIM)[0]:
                    store.delete_flow(metas[int(i)]["id"])
            fid = store.insert_flow(question, prose, files, vec)
            store.commit()
            return fid
    except Exception:                                       # noqa: BLE001
        log.debug("flow cache skipped", exc_info=True)
        return None


def match_flows(flow_metas: list[dict], FL: np.ndarray, qv: np.ndarray) -> list[dict]:
    """The read path — pure cosine, no LLM. Flows scoring >= FLOW_MIN_SIM (same
    (cos+1)/2 normalization as chunk scores), best FLOW_TOP_K."""
    if not flow_metas or FL.size == 0:
        return []
    sims = (FL @ qv + 1) / 2
    order = np.argsort(-sims)[:FLOW_TOP_K]
    out = []
    for i in order:
        if sims[int(i)] < FLOW_MIN_SIM:
            break
        m = flow_metas[int(i)]
        out.append({"question": m["question"], "text": m["text"],
                    "files": sorted(m["files"]), "score": round(float(sims[int(i)]), 4)})
    return out


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
        from . import providers
        tree = "\n".join(f"- {f}: {docline.get(f, '')}" for f in hubs)
        raw = providers.chat_text(
            providers.rerank_model(),
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
        from .ask import ask as ask_fn  # noqa: PLW0127
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


def prune_stale(store: Store) -> int:
    """Drop flows whose cited files changed sha or left the index. Called by
    index_repo after every (re)index, so flows always describe current code."""
    metas, _ = store.load_flows()
    current = {r[0]: r[1] for r in store.db.execute("SELECT path, sha FROM files")}
    dropped = 0
    for m in metas:
        if any(current.get(f) != sha for f, sha in m["files"].items()):
            store.delete_flow(m["id"])
            dropped += 1
    return dropped
