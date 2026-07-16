"""flows — self-caching workflow retrieval (OPT-IN, off by default).

This is a MODE the developer turns on per repo (`megabrain flows --enable`, or
implied by `--warm-flows`); the env var MEGABRAIN_FLOW_CACHE forces it on/off
globally. When OFF — the default — nothing here runs: `query`/`ask` behave
exactly as they did before, at zero cost. When ON:

Every `ask` synthesizes a cross-file walkthrough (a WORKFLOW: "VAD detects
speech → TurnController.on_vad_start → cancel TTS"). That synthesis is
expensive knowledge the engine used to throw away. This module caches it in
the index so the NEXT related question retrieves the whole flow at once:

  WRITE (ask time — LLM + one embed batch, both off the query path):
      after a successful spliced answer, the RENDERED body (prose + real code
      from disk) is stored with {cited file: sha} and TWO vectors from one
      batch call: question+prose (the ATTACH lane) and question-only (the
      SERVE lane — an identical question scores ~1.0 there, so prose length
      can never dilute it). Near-duplicate QUESTIONS (cos > 0.92 on the serve
      lane) replace the old entry. Fail-open: a cache error never breaks ask.

  READ (query time — cosine only, hard rule 1 intact), three tiers:
      qscore >= 0.88 (near-exact question, shas still current)
          -> SERVE the cached answer verbatim: no LLM, ~0 ms, zero cost.
             Measured: 6.9 s -> 0.02 s on a repeat ask (345x).
      score 0.62-0.88 (same workflow, different question)
          -> ATTACH: "KNOWN FLOW" section in the bundle + non-citable context
             for the narrator; it narrates fresh (and re-caches).
      below 0.62 -> nothing; plain retrieval.
      Flows never rank or displace files (the rule-3 analog); their source
      files append to RELATED only when missing — pure additions, so
      bundle_full can only rise.

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

FLOW_MIN_SIM = 0.62      # normalized (cos+1)/2 floor to ATTACH a flow as context
FLOW_SERVE_SIM = 0.88    # near-exact match → SERVE the cached answer, skip the LLM
FLOW_TOP_K = 2           # at most this many flows per query
FLOW_DEDUP_SIM = 0.92    # raw-vector cosine above this = same flow, replace
FLOW_TEXT_CAP = 14000    # chars of the rendered answer (prose+code) stored per flow
FLOW_FILE_ADDS = 3       # max missing flow-source files appended to RELATED

_CITE = re.compile(r"\[\[[^\]]*\]\]")
_CODE = re.compile(r"```.*?```", re.S)      # fenced code blocks
_META = "flow_cache"


def strip_code(text: str) -> str:
    """Prose without fenced code — for embedding + narrator context (the stored
    answer keeps the code for verbatim serving)."""
    return _CODE.sub("", _CITE.sub("", text)).strip()


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


def cache_flow(root: Path, question: str, answer: str, cited_files: list[str],
               emb=None) -> int | None:
    """Persist one ask answer as a retrievable flow. `answer` is the RENDERED
    walkthrough (prose + real code blocks spliced from disk) so a near-exact
    later question can be SERVED verbatim without an LLM. The embedding is over
    question + prose-only (code stripped) for clean matching. Returns the flow
    id, or None when skipped (mode off, nothing cited, any error — fail-open)."""
    if not enabled(root) or not cited_files or not answer.strip():
        return None
    try:
        if emb is None:
            from ..providers.embeddings import Embedder
            emb = Embedder()
        stored = answer.strip()[:FLOW_TEXT_CAP]              # keep code, for serving
        prose = strip_code(stored)                           # for embed + context
        with Store(Path(root)) as store:
            files = {}
            for f in sorted(set(cited_files)):
                p = Path(root) / f
                if p.is_file():
                    files[f] = hashlib.sha256(
                        p.read_text(encoding="utf-8", errors="replace").encode()).hexdigest()
            if not files:
                return None
            # two lanes, one batch call: attach vector (question+prose, semantic
            # recall) and serve vector (question-only, near-exact detection —
            # prose length can no longer dilute an identical question to ~0.83).
            V = emb.embed([f"{question}\n\n{prose}", question]).astype(np.float32)
            vec, qvec = V[0], V[1]
            # near-duplicate → replace: same/near-same QUESTION is the dedup key
            # (two narrations of one question must not accumulate)
            metas, _, Q = store.load_flows()
            if len(metas):
                qn = np.linalg.norm(Q, axis=1) * np.linalg.norm(qvec) + 1e-9
                sims = Q @ qvec / qn
                for i in np.where(sims > FLOW_DEDUP_SIM)[0]:
                    store.delete_flow(metas[int(i)]["id"])
            fid = store.insert_flow(question, stored, files, vec, qvec)
            store.commit()
            return fid
    except Exception:                                       # noqa: BLE001
        log.debug("flow cache skipped", exc_info=True)
        return None


def match_flows(flow_metas: list[dict], FL: np.ndarray, qv: np.ndarray,
                FLQ: np.ndarray | None = None) -> list[dict]:
    """The read path — pure cosine, no LLM. ATTACH lane: question+prose vectors,
    flows >= FLOW_MIN_SIM, best FLOW_TOP_K. Each match also carries `qscore`
    (query vs the flow's question-only vector) — the SERVE lane's signal — and
    its {file: sha} map so the caller can serve it verbatim safely."""
    if not flow_metas or FL.size == 0:
        return []
    sims = (FL @ qv + 1) / 2
    qsims = (FLQ @ qv + 1) / 2 if FLQ is not None and FLQ.size else None
    order = np.argsort(-sims)[:FLOW_TOP_K]
    out = []
    for i in order:
        if sims[int(i)] < FLOW_MIN_SIM:
            break
        m = flow_metas[int(i)]
        out.append({"question": m["question"], "text": m["text"],
                    "files": sorted(m["files"]), "sha": m["files"],
                    "score": round(float(sims[int(i)]), 4),
                    "qscore": round(float(qsims[int(i)]), 4) if qsims is not None else 0.0})
    return out


def serve_verbatim(root, flows: list[dict]) -> dict | None:
    """If a matched flow's QUESTION is a near-exact match for the query
    (qscore >= FLOW_SERVE_SIM — question-only vectors, so prose length can't
    dilute it) AND every cited file is still byte-identical to when it was
    cached, return it so `ask` answers WITHOUT an LLM: instant, zero cost, and
    never stale (the sha recheck guards the 60s window before an index would
    prune it). Else None, and ask narrates fresh (re-caching the result)."""
    for top in flows:
        if top.get("qscore", 0.0) < FLOW_SERVE_SIM:
            continue
        root = Path(root)
        ok = all((root / f).is_file() and hashlib.sha256(
                 (root / f).read_text(encoding="utf-8", errors="replace").encode()).hexdigest() == sha
                 for f, sha in top["sha"].items())
        if ok:
            return top
    return None
