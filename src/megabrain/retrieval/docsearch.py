"""docsearch — section-level semantic hits shaped for a docs-site search box.

A projection of retrieval (not an HTTP concern — it used to live inside the
serve-api handler, which trapped it to one transport). Flattens a bundle to
per-section results in docs-web's SearchResult shape, deduped to the best hit
per page (slug), with markdown cleaned to prose for display.

Result groups (sidebar sections) are per-deployment config, not engine
knowledge:
  <repo>/.megabrain/docsearch.json   {"api/": "SDK API", "guides/": "Guides"}
  MEGABRAIN_DOCSEARCH_GROUPS         same JSON object, env fallback
Slug prefixes match in declaration order; no match -> "Docs".
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .bundle import search_with_state
from .state import SearchState


def load_groups(root: Path) -> tuple[tuple[str, str], ...]:
    raw = None
    cfg = root / ".megabrain" / "docsearch.json"
    if cfg.exists():
        raw = cfg.read_text(errors="replace")
    elif os.environ.get("MEGABRAIN_DOCSEARCH_GROUPS"):
        raw = os.environ["MEGABRAIN_DOCSEARCH_GROUPS"]
    if not raw:
        return ()
    try:
        d = json.loads(raw)
        return tuple((str(k), str(v)) for k, v in d.items())
    except (json.JSONDecodeError, AttributeError):
        return ()


# Markdown chunk text is raw (YAML frontmatter, '#' headings, fences, backticks).
# Clean it for display so the snippet reads as prose and the title has no markup.
_FM = re.compile(r"\A﻿?---[ \t]*\n.*?\n---[ \t]*\n+", re.S)


def _strip_fm(text: str) -> str:
    return _FM.sub("", text, count=1)


def _clean_inline(t: str) -> str:
    t = re.sub(r"`([^`]+)`", r"\1", t)                 # `code` -> code
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)     # [text](url) -> text
    t = re.sub(r"[*_~]", "", t)                         # bold / italic / strike
    return t.strip()


def _snippet(text: str, n: int = 160) -> str:
    """Frontmatter + markdown stripped to readable prose (keeps code text)."""
    t = _strip_fm(text)
    t = re.sub(r"```+[A-Za-z0-9_-]*\n?", " ", t)        # fence markers out, code stays
    t = re.sub(r"^[ \t]*#{1,6}\s+.*$", "", t, flags=re.M)  # heading lines out
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"[*_~>#|]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return (t[:n].rstrip() + "…") if len(t) > n else t


def _context(text: str, n: int = 2000) -> str:
    """Frontmatter + leading H1 stripped; markdown kept (the preview renders it)."""
    t = _strip_fm(text)
    t = re.sub(r"\A#\s+.+\n+", "", t)                  # drop leading H1 (shown as title)
    t = t.strip()
    return (t[:n].rstrip() + "\n\n…") if len(t) > n else t


def _group(slug: str, groups: tuple[tuple[str, str], ...]) -> str:
    s = slug.lstrip("/")
    for prefix, name in groups:
        if s.startswith(prefix):
            return name
    return "Docs"


def _slug(relpath: str) -> str:
    """docs/foo/bar.md -> /foo/bar ; index.md -> / (matches build-search-index)."""
    rel = relpath
    for ext in (".md", ".markdown", ".mdx"):
        if rel.endswith(ext):
            rel = rel[: -len(ext)]
            break
    if rel.startswith("docs/"):       # repo root may sit above the docs dir
        rel = rel[len("docs/"):]
    if rel in ("index", ""):
        return "/"
    return "/" + rel


def _title(relpath: str, chunk: dict) -> str:
    bc = (chunk.get("breadcrumb") or "").strip()
    if bc:
        # breadcrumb separator is ' > ' (markdown.py _crumb); path crumbs and
        # heading text may contain '/', so split ONLY on ' > '. Keep the heading
        # path after the '<file>.md' crumb — the rest duplicates the slug.
        segs = [s.strip() for s in bc.split(" > ") if s.strip()]
        cut = -1
        for i, s in enumerate(segs):
            if s.endswith((".md", ".markdown", ".mdx")):
                cut = i
        headings = segs[cut + 1:] if cut >= 0 else segs[-1:]
        headings = [h for h in (_clean_inline(s.lstrip("#").strip()) for s in headings) if h]
        if headings:
            return " › ".join(headings[:3])
    nm = _clean_inline((chunk.get("name") or "").lstrip("#").strip())
    if nm:
        return nm
    tail = _slug(relpath).rstrip("/").rsplit("/", 1)[-1] or "Overview"
    return tail.replace("-", " ")


def docsearch(state: SearchState, q: str, limit: int = 15,
              groups: tuple[tuple[str, str], ...] = ()) -> list[dict]:
    """Flatten retrieval to section-level hits in docs-web's SearchResult shape,
    deduped to the best hit per page (slug)."""
    res = search_with_state(state, q)
    hits: list[tuple[str, dict, float]] = []
    for t in res["tier1"]:
        for c in t["chunks"]:
            hits.append((t["file"], c, float(c.get("score", t["score"]))))
    for t in res["tier2"]:
        bc = t.get("best_chunk")
        if bc:
            hits.append((t["file"], bc, float(t.get("score", 0))))
    if not hits:
        return []
    top = max(h[2] for h in hits) or 1.0
    best_by_slug: dict[str, dict] = {}
    for relpath, chunk, score in hits:
        slug = _slug(relpath)
        raw = chunk.get("text") or ""
        entry = {
            "title": _title(relpath, chunk),
            "slug": slug,
            "snippet": _snippet(raw),
            "context": _context(raw),
            "score": round(score / top * 100),
            "group": _group(slug, groups),
        }
        prev = best_by_slug.get(slug)
        if prev is None or entry["score"] > prev["score"]:
            best_by_slug[slug] = entry
    return sorted(best_by_slug.values(), key=lambda e: -e["score"])[:limit]
