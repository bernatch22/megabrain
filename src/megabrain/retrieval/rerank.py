"""LLM rerank over the pruned signal list — the `llm_prune` lane.

The deterministic prune is recall-safe by design: every bundle file contributes
its best chunk, so files that merely SHARE VOCABULARY with the query (tests,
eval scripts, A/B gates) survive as "signal" and bloat the output. Cosine can't
tell "implements scoring" from "tests scoring". This lane fixes exactly that:
a cheap LLM sees a COMPACT view of the candidates (ids + spans + names, no
bodies, ~2K tokens) and returns only the relevant ids, ordered. The engine then
keeps/reorders its own verbatim chunks — the model selects, it never writes
code (same anti-hallucination stance as ask's citation splicing).

Fail-open everywhere: no key, timeout, malformed reply, unknown ids -> the
deterministic result is returned untouched. The LLM is an optimization, never
a dependency.
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

_PROMPT = """You are reranking code-search results. Question:

{question}

Candidate chunks (id · file:lines · symbols · hint):
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


def _hint(c: dict) -> str:
    """One short line of content per candidate: the first non-empty line of the
    chunk (usually a docstring/signature). Compact view only — never bodies."""
    for ln in (c.get("text") or "").splitlines():
        ln = ln.strip().strip('"').strip("'")
        if ln:
            return ln[:90]
    return ""


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
    m = model or rerank_model()
    listing = "\n".join(
        f'[{c["id"]}] {c["file"]}:L{c["start_line"]}-{c["end_line"]} · '
        f'{c.get("name") or "?"} ({c.get("kind") or "?"}) · {_hint(c)}'
        for c in chunks)
    try:
        from .. import providers
        reply = providers.chat_text(m, _PROMPT.format(question=question,
                                                      listing=listing),
                                    RERANK_MAX_TOKENS, timeout=RERANK_TIMEOUT)
        arr = re.search(r"\[[\d,\s]*\]", reply)
        ids = [int(x) for x in json.loads(arr.group(0))] if arr else []
        by_id = {c["id"]: c for c in chunks}
        kept = [by_id[i] for i in ids if i in by_id]
        if not kept:                      # model returned nothing usable
            raise ValueError(f"no valid ids in reply: {reply[:120]!r}")
        dropped = [c for c in chunks if c["id"] not in set(ids)]
        res["chunks"] = kept
        res["kept"] = len(kept)
        res["pruned"] = res.get("pruned", 0) + len(dropped)
        if "noise" in res:
            res["noise"] = dropped + res["noise"]
        res["reranked"] = {"model": m, "kept": len(kept),
                           "dropped": len(dropped),
                           "ms": int((time.time() - t0) * 1000)}
    except Exception:
        log.debug("llm rerank failed open", exc_info=True)
        res["reranked"] = False
    return res
