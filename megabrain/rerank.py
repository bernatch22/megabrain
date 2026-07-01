"""Optional listwise ORDER rerank (off by default — adds ~1-2s).

Reorders the top candidate files; never adds or removes (recall is
untouched by construction). Fail-open: any error -> original order.
SweRankLLM-style listwise: +5-10 Acc@1 in the literature.
"""

from __future__ import annotations

import json
import re

from . import providers

MODEL = providers.RERANK_MODEL


def _key() -> str | None:
    """Back-compat shim: single OpenRouter key (env or ~/.zshrc)."""
    return providers.find_key(required=False)


def haiku_order(query: str, candidates: list[dict]) -> list[int]:
    """candidates: [{file, evidence}] -> permutation of indices (fail-open: identity)."""
    ident = list(range(len(candidates)))
    key = providers.find_key(required=False)
    if not key or len(candidates) < 2:
        return ident
    lines = [f"[{i}] {c['file']}\n    {c['evidence'][:400]}" for i, c in enumerate(candidates)]
    prompt = f"""Order these candidate files by how likely each must be EDITED to resolve the issue. Files are pre-ranked by a retriever; only reorder when the evidence clearly justifies it.

ISSUE:
{query[:5000]}

CANDIDATES:
{chr(10).join(lines)}

Rules:
- The fix location is usually the implementation, not callers, configs, or re-export shims.
- A file named in a traceback frame near the error is a strong signal.
- Include EVERY index exactly once.

Reply ONLY a JSON array, e.g. [2,0,1,...]"""
    try:
        text = providers.chat_text(MODEL, prompt, max_tokens=120, key=key, timeout=30)
        m = re.search(r"\[[\d,\s]*\]", text)
        if not m:
            return ident
        order = [i for i in json.loads(m.group(0)) if isinstance(i, int) and 0 <= i < len(candidates)]
        seen = set(order)
        order += [i for i in ident if i not in seen]  # permutation guarantee
        return order
    except Exception:
        return ident
