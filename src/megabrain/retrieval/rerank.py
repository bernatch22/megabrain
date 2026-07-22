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


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")


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
        s = sum(len(t) for t in {w.lower() for w in _IDENT.findall(ln)} & qtok)
        if s > score:
            best, score = ln, s
    return (best or lines[0])[:90]


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
    if (model is None and not os.environ.get("MEGABRAIN_RERANK_MODEL")
            and providers.chat_provider() == "claude"
            and (providers._is_local(providers.CHAT_BASE_URL)
                 or providers.find_key(required=False))):
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
    listing = "\n".join(
        f'[{c["id"]}] {c["file"]}:L{c["start_line"]}-{c["end_line"]} · '
        f'{c.get("name") or "?"} ({c.get("kind") or "?"}) · {_hint(c, question)}'
        for c in chunks)
    try:
        reply = chat(m, _PROMPT.format(question=question, listing=listing),
                     RERANK_MAX_TOKENS, timeout=RERANK_TIMEOUT)
        arr = re.search(r"\[[\d,\s]*\]", reply)
        ids = [int(x) for x in json.loads(arr.group(0))] if arr else []
        by_id = {c["id"]: c for c in chunks}
        kept = [by_id[i] for i in ids if i in by_id]
        if not kept:                      # model returned nothing usable
            raise ValueError(f"no valid ids in reply: {reply[:120]!r}")
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
                           "dropped": len(dropped),
                           "ms": int((time.time() - t0) * 1000)}
    except Exception:
        log.debug("llm rerank failed open", exc_info=True)
        res["reranked"] = False
    return res
