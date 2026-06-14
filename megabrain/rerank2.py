"""Listwise rerank v2 — code evidence + parallel majority vote.

vs v1: candidates carry actual code (best chunk, trimmed), pool is deeper,
and N parallel Haiku votes are merged by mean rank (LocAgent-style MRR
merge). Still permute-only: recall untouched. Fail-open everywhere.
"""

from __future__ import annotations

import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .rerank import _key

MODEL = "claude-haiku-4-5"


def _one_vote(key: str, query: str, lines: list[str], n: int) -> list[int] | None:
    prompt = f"""Rank candidate files by how likely each must be EDITED to fix this issue.

ISSUE:
{query[:5000]}

CANDIDATES (pre-ranked by a retriever; each shows its best-matching code):
{chr(10).join(lines)}

Rules:
- The fix lives in the implementation, not in callers/configs/re-export shims.
- Traceback frames near the error are strong; but the CAUSE may be one hop away
  (a function the frame calls) — prefer the cause when the code shows it.
- Include EVERY index exactly once, best first.

Reply ONLY a JSON array of all {n} indices, e.g. [2,0,5,...]"""
    body = {"model": MODEL, "max_tokens": 220, "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as res:
            d = json.loads(res.read())
        m = re.search(r"\[[\d,\s]*\]", d["content"][0]["text"])
        if not m:
            return None
        order = [i for i in json.loads(m.group(0)) if isinstance(i, int) and 0 <= i < n]
        seen = set(order)
        return order + [i for i in range(n) if i not in seen]
    except Exception:
        return None


def haiku_order2(query: str, candidates: list[dict], votes: int = 3) -> list[int]:
    """candidates: [{file, code}] -> permutation (identity on failure)."""
    n = len(candidates)
    ident = list(range(n))
    key = _key()
    if not key or n < 2:
        return ident
    lines = []
    for i, c in enumerate(candidates):
        code = c.get("code", "")[:550].replace("\n", "\n    ")
        lines.append(f"[{i}] {c['file']}\n    {code}")
    with ThreadPoolExecutor(max_workers=votes) as ex:
        results = list(ex.map(lambda _: _one_vote(key, query, lines, n), range(votes)))
    results = [r for r in results if r]
    if not results:
        return ident
    # mean-rank merge; retriever order (identity) breaks ties
    score = [0.0] * n
    for r in results:
        for rank, i in enumerate(r):
            score[i] += rank
    by_votes = sorted(ident, key=lambda i: (score[i], i))
    # bounded demotion: rise freely, fall at most MAX_FALL below retriever rank
    MAX_FALL = 1
    out: list[int] = []
    remaining = by_votes[:]
    for pos in range(n):
        forced = [i for i in remaining if i + MAX_FALL <= pos]
        pick = min(forced) if forced else remaining[0]
        out.append(pick)
        remaining.remove(pick)
    return out
