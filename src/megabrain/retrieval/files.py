"""File serving — the repo-root containment boundary.

get_code() is the one place megabrain reads a raw source file off disk for a
caller-supplied relpath, so it is a SECURITY boundary: `relpath` is
attacker-adjacent when served over HTTP (serve-api /get) or MCP, and the
containment check below is what stops `../../etc/passwd` from escaping the
repo. Keep that check first and unconditional.
"""

from __future__ import annotations

from pathlib import Path

from ..storage.store import Store
from .render import lang_of


def get_code(root: Path, relpath: str, symbol: str | None = None) -> str:
    root = Path(root).resolve()
    p = (root / relpath).resolve()
    # containment check: `relpath` is attacker-adjacent when served over HTTP
    # (serve.py /get) or MCP — `../../etc/passwd` must never escape the repo.
    if not p.is_relative_to(root) or not p.exists():
        return f"not found: {relpath}"
    src = p.read_text(encoding="utf-8", errors="replace")
    if not symbol:
        return f"```{lang_of(relpath)}\n{src}\n```"
    with Store(Path(root)) as store:
        syms = [s for s in store.symbols_for(relpath)
                if s["name"] == symbol or s["name"].endswith("." + symbol)]
    if not syms:
        return f"symbol {symbol} not found in {relpath}"
    lines = src.splitlines(keepends=True)
    out = []
    for s in syms:
        code = "".join(lines[s["line"] - 1:s["end_line"]])
        out.append(f'# {relpath} > {s["signature"]} (L{s["line"]}-{s["end_line"]})\n'
                   f'```{lang_of(relpath)}\n{code}```')
    return "\n\n".join(out)
