"""Optional listwise LLM rerank (`--best`) — code evidence + parallel majority vote.

Candidates carry actual code (best chunk, trimmed); N parallel votes are merged
by mean rank (LocAgent-style MRR merge). Permute-only: never adds or removes
candidates, so recall is untouched by construction. Fail-open everywhere: any
error -> retriever order.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor

from . import providers


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
    try:
        text = providers.chat_text(providers.rerank_model(), prompt,
                                   max_tokens=220, key=key, timeout=45)
        m = re.search(r"\[[\d,\s]*\]", text)
        if not m:
            return None
        order = [i for i in json.loads(m.group(0)) if isinstance(i, int) and 0 <= i < n]
        seen = set(order)
        return order + [i for i in range(n) if i not in seen]
    except Exception:
        return None


def llm_order(query: str, candidates: list[dict], votes: int = 3) -> list[int]:
    """candidates: [{file, code}] -> permutation (identity on failure)."""
    n = len(candidates)
    ident = list(range(n))
    key = providers.find_chat_key(required=False)
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
