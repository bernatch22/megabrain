"""LLM rerank over the pruned signal list — the `llm_prune` lane.

The deterministic prune is recall-safe by design: every bundle file contributes
its best chunk, so files that merely SHARE VOCABULARY with the query (tests,
eval scripts, A/B gates) survive as "signal" and bloat the output. Cosine can't
tell "implements scoring" from "tests scoring". This lane fixes exactly that:
a cheap LLM judges the candidates and returns only the relevant ids, ordered.
The engine then keeps/reorders its own verbatim chunks — the model selects, it
never writes code (same anti-hallucination stance as ask's citation splicing).

WHAT THE JUDGE SEES is lane-dependent, and it was measured, not assumed
(6 ground-truth queries x 4 views x 3 reps, nx/rails/megabrain indexes):

  view                        target kept   rank(med)   note
  1 query-aware line              18/18        2        never misses, judge is humble
  6-line window                   12/18        -        partial evidence invites
  full bodies, ONE call           15/18        1          confident WRONG exclusion
  full bodies, batches of 8       18/18        1        local competition per call

Partial evidence is worse than little evidence: on the cross-subsystem query
(rails#57197 — the answering file never mentions the state the question asks
about) every mid-size view dropped the answer 3/3, while the 1-line view and
the batched view kept it 3/3. Small candidate pools per call keep the judge
from over-confidently ruling files out; the price is a looser keep (median 7
vs 4 of ~29 survive) — and completeness beats ordering, so that trade is
taken. Hence:

  - remote HTTP lane (OpenRouter/compat): full bodies, batches of
    RERANK_BATCH chunks, parallel calls (~34K tokens ~ $0.009/rerank on
    flash-lite, +~300ms over the 1-line view)
  - local endpoint (Ollama serializes; big prompts choke it) and the claude
    CLI lane (~18s per spawned call): the 1-line query-aware view, one call

Fail-open everywhere, all-or-nothing across batches: no key, timeout, one
failed batch, malformed reply, unknown ids -> the deterministic result is
returned untouched. The LLM is an optimization, never a dependency.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time

log = logging.getLogger(__name__)

RERANK_MAX_TOKENS = 300
RERANK_TIMEOUT = 30
# Candidates per judging call on the bodies lane. 8 is the measured sweet
# spot: one 29-candidate call missed 3/18 targets the 8-per-call batches all
# kept — small pools stop the judge from confidently ruling files out.
RERANK_BATCH = 8

_PROMPT = """You are reranking code-search results. Question:

{question}

Candidate chunks (id · file:lines · symbols · {view}):
{listing}

Return ONLY a JSON array of the ids worth reading to answer the question,
most relevant first. Drop chunks that are merely vocabulary-related: test
files, eval/benchmark scripts, docs restating the code, and tangential
subsystems. Keep every chunk that implements or directly configures the
mechanism asked about. Example output: [12, 7, 31]"""


def rerank_model() -> str:
    """`MEGABRAIN_RERANK_MODEL` or the ask default — reranking is cheap, any
    fast model works."""
    from .. import providers
    return os.environ.get("MEGABRAIN_RERANK_MODEL") or providers.ask_model()


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")


def _score_line(ln: str, qtok: set[str]) -> int:
    """Shared-identifier LENGTH between a line and the question — length, not
    count, so one rare long name (analyzeSourceFiles) outweighs several
    generic short ones (project + graph + read). Shared by the rerank card
    hint and the render's cap-window picker."""
    return sum(len(t) for t in {w.lower() for w in _IDENT.findall(ln)} & qtok)


def _hint(c: dict, question: str = "") -> str:
    """One short line of content per candidate: the chunk line sharing the most
    identifier characters with the question, else the first non-empty line
    (usually a docstring/signature). Compact view only — never bodies.

    Query-aware because the judge can only weigh what the card shows: a
    whole-file chunk led with its import line while the flag the question
    named sat at L36, and the rerank dropped the one file that answered it
    (nx#35656, `analyzeSourceFiles`). Scoring by total shared-identifier
    LENGTH lets one rare long name outweigh several generic short ones."""
    lines = [ln.strip().strip('"').strip("'")
             for ln in (c.get("text") or "").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return ""
    qtok = {t.lower() for t in _IDENT.findall(question)}
    best, score = None, 0
    for ln in lines:
        s = _score_line(ln, qtok)
        if s > score:
            best, score = ln, s
    return (best or lines[0])[:90]


def _listing(chunks: list[dict], question: str, bodies: bool) -> str:
    """The candidate listing for one judging call: header per chunk, then the
    full verbatim body (bodies lane) or the 1-line query-aware hint."""
    rows = []
    for c in chunks:
        head = (f'[{c["id"]}] {c["file"]}:L{c["start_line"]}-{c["end_line"]} · '
                f'{c.get("name") or "?"} ({c.get("kind") or "?"})')
        rows.append(f'{head}\n{c.get("text") or ""}' if bodies
                    else f'{head} · {_hint(c, question)}')
    return "\n".join(rows)


def _parse_ids(reply: str) -> list[int]:
    """The first JSON int array in the reply. No array at all is a protocol
    failure and raises (-> fail open); an empty `[]` is a legitimate verdict."""
    arr = re.search(r"\[[\d,\s]*\]", reply)
    if not arr:
        raise ValueError(f"no id array in reply: {reply[:120]!r}")
    return [int(x) for x in json.loads(arr.group(0))]


def _merge_round_robin(per_batch: list[list[int]]) -> list[int]:
    """Interleave each batch's ranking by position: every judge's #1 outranks
    any judge's #2. Batches are score-ordered slices, so within a position the
    earlier batch held the stronger deterministic candidates and stays first.
    Measured: median target rank 1.0 across the eval, vs 2.0 for the 1-line
    single call."""
    merged, i = [], 0
    while any(i < len(b) for b in per_batch):
        for b in per_batch:
            if i < len(b):
                merged.append(b[i])
        i += 1
    return merged


def llm_rerank(res: dict, question: str, model: str | None = None) -> dict:
    """Filter + reorder a prune_search result via one buffered LLM call.

    Kept chunks are reordered to the model's ranking; dropped ones move to
    `noise` (created if absent) so nothing is silently destroyed. Annotates
    `res["reranked"] = {model, kept, dropped, ms}` on success, `False` on any
    failure (fail-open: the deterministic result is the floor, never worse)."""
    chunks = res.get("chunks") or []
    if len(chunks) < 2:
        res["reranked"] = False
        return res
    t0 = time.time()
    from .. import providers
    m = model or rerank_model()
    # Rerank is a mechanical id filter, so it takes the FASTEST lane available,
    # not the narration provider: on the claude provider each chat_text spawns
    # the Claude CLI — measured ~18s per rerank from the MCP server vs ~0.7s on
    # the OpenAI-compat lane, for identical selections (both kept the bug file,
    # both dropped the noise, 3/3 queries). An explicit model pin (arg or
    # MEGABRAIN_RERANK_MODEL) is respected and keeps the provider routing; and
    # with no OpenRouter key or local endpoint there is no fast lane, so the
    # claude provider remains the (slow but working) fallback.
    chat = providers.chat_text
    is_claude = providers.chat_provider() == "claude"
    fast_lane = (model is None and not os.environ.get("MEGABRAIN_RERANK_MODEL")
                 and is_claude
                 and (providers._is_local(providers.CHAT_BASE_URL)
                      or providers.find_key(required=False)))
    if fast_lane:
        from functools import partial

        # key resolved HERE for the fast lane: find_chat_key() would return the
        # "claude" sentinel (the resolved provider is still claude) and the
        # request would go out with no real credential — bit on first test
        # (openrouter 401 "Missing Authentication header").
        key = providers._key_for(providers.CHAT_BASE_URL,
                                 os.environ.get("MEGABRAIN_CHAT_API_KEY"),
                                 required=False)
        chat = partial(providers._REGISTRY["openrouter"].chat_text, key=key)
        m = providers.FAST_CHAT_MODEL
    # Bodies go to the judge only on a REMOTE HTTP lane: a local server
    # (Ollama) serializes and chokes on parallel ~9K-token prompts, and the
    # claude CLI lane spawns a ~18s process per call — both stay on the
    # 1-line view, one call. Same local-vs-cloud stance as embed concurrency.
    http_lane = (not is_claude) or fast_lane
    bodies = http_lane and not providers._is_local(providers.CHAT_BASE_URL)
    batch = max(1, int(os.environ.get("MEGABRAIN_RERANK_BATCH",
                                      str(RERANK_BATCH))))
    try:
        view = "code" if bodies else "hint"

        def _judge(group: list[dict]) -> list[int]:
            return _parse_ids(chat(
                m, _PROMPT.format(question=question, view=view,
                                  listing=_listing(group, question, bodies)),
                RERANK_MAX_TOKENS, timeout=RERANK_TIMEOUT))

        n_calls = 1
        if bodies and len(chunks) > batch:
            from concurrent.futures import ThreadPoolExecutor
            groups = [chunks[i:i + batch] for i in range(0, len(chunks), batch)]
            n_calls = len(groups)
            with ThreadPoolExecutor(max_workers=len(groups)) as ex:
                per_batch = list(ex.map(_judge, groups))   # any failure -> open
            ids = _merge_round_robin(per_batch)
        else:
            ids = _judge(chunks)
        by_id = {c["id"]: c for c in chunks}
        kept = [by_id[i] for i in ids if i in by_id]
        if not kept:                      # model kept nothing usable
            raise ValueError(f"no valid ids kept: {ids!r}")
        dropped = [c for c in chunks if c["id"] not in set(ids)]
        # A dropped TEST file is not noise — it is often the SPEC. Field case
        # (rails#57197): the subsystem's test file pinned instance identity
        # (`successfully_enqueued?` must flip on the same object), which is
        # what ruled out the issue author's dup-based fix — and the rerank had
        # dropped it invisibly. Tests stay out of the signal list (they crowd
        # implementation by shared vocabulary — the reason the prompt drops
        # them) but surface in their own labeled section: structure, not
        # deletion, same stance as the CORE/RELATED map.
        from .scoring import _is_test_path
        tests = [c for c in dropped if _is_test_path(c["file"])]
        noise = [c for c in dropped if not _is_test_path(c["file"])]
        res["chunks"] = kept
        res["tests"] = tests
        res["kept"] = len(kept)
        res["pruned"] = res.get("pruned", 0) + len(noise)
        if "noise" in res:
            res["noise"] = noise + res["noise"]
        res["reranked"] = {"model": m, "kept": len(kept),
                           "dropped": len(dropped), "view": view,
                           "batches": n_calls,
                           "ms": int((time.time() - t0) * 1000)}
    except Exception:
        log.debug("llm rerank failed open", exc_info=True)
        res["reranked"] = False
    return res
