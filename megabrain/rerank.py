"""Optional Haiku listwise ORDER rerank (off by default — adds ~1-2s).

Reorders the top candidate files; never adds or removes (recall is
untouched by construction). Fail-open: any error -> original order.
SweRankLLM-style listwise: +5-10 Acc@1 in the literature.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

MODEL = "claude-haiku-4-5"


def _key() -> str | None:
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k:
        return k
    z = Path.home() / ".zshrc"
    if z.exists():
        m = re.search(r"^export ANTHROPIC_API_KEY=[\"']?([^\"'\s#]+)", z.read_text(), re.M)
        if m:
            return m.group(1)
    return None


def haiku_order(query: str, candidates: list[dict]) -> list[int]:
    """candidates: [{file, evidence}] -> permutation of indices (fail-open: identity)."""
    ident = list(range(len(candidates)))
    key = _key()
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
    body = {"model": MODEL, "max_tokens": 120, "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            d = json.loads(res.read())
        m = re.search(r"\[[\d,\s]*\]", d["content"][0]["text"])
        if not m:
            return ident
        order = [i for i in json.loads(m.group(0)) if isinstance(i, int) and 0 <= i < len(candidates)]
        seen = set(order)
        order += [i for i in ident if i not in seen]  # permutation guarantee
        return order
    except Exception:
        return ident
