"""Deterministic issue parsing + entity grounding (LocAgent-style, no LLM).

Extracts from long issue-like queries:
- stack-trace frames  File "x.py", line 123, in foo  -> (file, line) tier 0
- explicit .py paths                                  -> file tier 1
- `backticked` identifiers and Dotted.Names           -> symbol tier 2

Grounding: suffix path match against indexed files; symbol cascade
exact -> lowercase -> dotted-suffix with prefix filter. All ~1ms.
"""

from __future__ import annotations

import re

_FRAME = re.compile(r'File "([^"]+)", line (\d+)(?:, in (\w+))?')
_PYPATH = re.compile(r"[\w/.\-]+\.py\b")
_TICKED = re.compile(r"`([^`\n]{2,80})`")
_DOTTED = re.compile(r"\b[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+\b")


def _ground_path(p: str, files: list[str]) -> str | None:
    """Longest suffix match of p against indexed file paths."""
    p = p.lstrip("./")
    cands = [f for f in files if f.endswith(p)]
    if not cands:
        parts = p.split("/")
        for i in range(1, min(len(parts), 4)):
            tail = "/".join(parts[i:])
            cands = [f for f in files if f.endswith("/" + tail) or f == tail]
            if cands:
                break
    return min(cands, key=len) if cands else None


class SymbolIndex:
    def __init__(self, symbols: list[dict]):
        # symbols: {file, name, line, end_line}
        self.exact: dict[str, list[dict]] = {}
        self.lower: dict[str, list[dict]] = {}
        for s in symbols:
            leaf = s["name"].split(".")[-1]
            self.exact.setdefault(leaf, []).append(s)
            self.lower.setdefault(leaf.lower(), []).append(s)

    def ground(self, term: str) -> list[dict]:
        term = re.sub(r"^(class|def|function|method)\s+", "", term.strip())
        term = term.split("(")[0].strip()
        if not term or len(term) < 3:
            return []
        if "." in term:
            *prefix, leaf = term.split(".")
            cands = self.exact.get(leaf) or self.lower.get(leaf.lower()) or []
            filt = [s for s in cands
                    if all(p.lower() in re.split(r"[./]", (s["file"] + "/" + s["name"]).lower())
                           for p in prefix)]
            return (filt or cands)[:8]
        return (self.exact.get(term) or self.lower.get(term.lower()) or [])[:8]


def query_variants(text: str) -> list[str]:
    """Deterministic views of an issue for ensemble retrieval (LocAgent T7).
    Returns [title, tracebacks, fenced code, identifier bag] — non-empty ones."""
    out = []
    title = text.strip().splitlines()[0][:300] if text.strip() else ""
    if len(title) > 15:
        out.append(title)
    frames = _FRAME.findall(text)
    if frames:
        out.append("\n".join(f'File "{f}", line {ln}, in {fn}' for f, ln, fn in frames[:12]))
    fenced = re.findall(r"```(?:\w*\n)?(.*?)```", text, re.S)
    code = "\n".join(fenced)[:3000].strip()
    if len(code) > 40:
        out.append(code)
    idents = [t for t in set(_TICKED.findall(text)) | set(_DOTTED.findall(text)[:40])
              if 3 <= len(t) <= 60]
    if len(idents) >= 3:
        out.append(" ".join(sorted(idents)[:50]))
    return out


def parse_issue(text: str, files: list[str], symbols: list[dict]) -> dict:
    """Returns {'pin_files': {file: tier}, 'pin_spans': [(file, lo, hi)]}.
    tier 0 = traceback frame (strongest), 1 = explicit path, 2 = identifier."""
    idx = SymbolIndex(symbols)
    by_file: dict[str, list[dict]] = {}
    for s in symbols:
        by_file.setdefault(s["file"], []).append(s)

    pin: dict[str, int] = {}
    spans: list[tuple[str, int, int]] = []

    def add(f: str, tier: int):
        if f and (f not in pin or tier < pin[f]):
            pin[f] = tier

    for m in _FRAME.finditer(text):
        f = _ground_path(m.group(1), files)
        if not f:
            continue
        add(f, 0)
        line = int(m.group(2))
        encl = [s for s in by_file.get(f, [])
                if s["line"] <= line <= s["end_line"]]
        if encl:
            s = min(encl, key=lambda s: s["end_line"] - s["line"])
            spans.append((f, s["line"], s["end_line"]))

    for m in _PYPATH.finditer(text):
        f = _ground_path(m.group(0), files)
        if f:
            add(f, 1)

    terms = set(_TICKED.findall(text)) | set(_DOTTED.findall(text)[:40])
    for t in terms:
        for s in idx.ground(t)[:4]:
            add(s["file"], 2)
            spans.append((s["file"], s["line"], s["end_line"]))

    return {"pin_files": pin, "pin_spans": spans}
