"""Markdown/doc chunker — no tree-sitter, no LLM. QMD-style scored break points.

Adopted from github.com/tobi/qmd's chunking idea (we skip its LLM stages — our
retrieval is LLM-free): instead of rigid heading splits, score candidate cut
lines (H1=100, H2=90, H3=80, fenced-code boundary=80, paragraph start=20) and
pick the highest-scoring cut near a target size, so chunks are heading-aligned
and never cut mid-section. Small sections merge until they reach the budget;
large sections split at the best interior break.

Guarantees match the code chunkers:
- Chunks are a line partition: no gaps, no overlaps, full coverage.
- Each chunk carries a breadcrumb (repo > path > # H1 > ## H2 — the heading
  stack at the chunk start), feeding the embedder via embed_text.
- Headings become symbols (kind "heading", qualified by stack); the heading
  outline is the skeleton.
- Budget is the same non-whitespace-char budget as code, so docs and code share
  one notion of chunk size.
"""

from __future__ import annotations

import re

from .base import DEFAULT_BUDGET, Chunk, FileResult, Symbol, nws

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def qmd_cut(lines, start: int, end: int, score, forbidden, budget: int):
    """Greedy QMD break selection over [start, end] (1-based inclusive): walk
    forward, and near the size target pick the highest-scoring allowed cut line.
    Returns contiguous (s, e) ranges that exactly partition the region.

    Shared by the markdown chunker (start=1, end=total) and the legacy-PHP
    chunker (per flow section) — `score`/`forbidden` are absolute-line-indexed."""
    pre = {start - 1: 0}
    acc = 0
    for i in range(start, end + 1):
        acc += nws(lines[i - 1])
        pre[i] = acc

    def size(s, e):
        return pre[e] - pre[s - 1]

    T = budget
    W = max(T // 2, 1)
    ranges = []
    s = start
    while s <= end:
        if size(s, end) <= T + W:
            ranges.append((s, end))
            break
        best_j, best_key = None, None
        j = s + 1
        while j <= end:
            sz = size(s, j - 1)
            if sz > T + W:
                break
            if sz >= T - W and not forbidden[j]:
                key = (score[j], -abs(sz - T))   # best score, then nearest target
                if best_key is None or key > best_key:
                    best_key, best_j = key, j
            j += 1
        if best_j is None:
            # no break in window (e.g. a huge code block): hard cut at the
            # first line past target that isn't forbidden
            j = s + 1
            while j <= end and (size(s, j - 1) < T or forbidden[j]):
                j += 1
            if j > end:
                ranges.append((s, end))
                break
            best_j = j
        ranges.append((s, best_j - 1))
        s = best_j
    return ranges


class MarkdownChunker:
    def __init__(self, budget: int = DEFAULT_BUDGET, repo: str = ""):
        self.budget = budget
        self.repo = repo

    def chunk_file(self, relpath: str, source: str) -> FileResult:
        lines = source.splitlines(keepends=True)
        total = len(lines)
        crumb_base = f"{self.repo} > {relpath}" if self.repo else relpath
        if total == 0:
            return FileResult(relpath, [], [], "", True, 0)

        fm_end, title = self._frontmatter(lines)
        heading, score, forbidden = self._scan(lines, total, fm_end)
        ranges = self._cut(lines, total, score, forbidden)

        chunks = []
        for s, e in ranges:
            stack = self._stack_at(heading, s)
            crumb = self._crumb(crumb_base, title, stack)
            if stack:
                kind = "section"
                name = " > ".join(t for _, t in stack)
            else:
                kind = "doc"
                name = title or None
            chunks.append(Chunk(relpath, kind, name, s, e,
                                "".join(lines[s - 1:e]), crumb).finalize())

        symbols = self._symbols(relpath, heading, total)
        skeleton = self._skeleton(relpath, heading, total)
        return FileResult(relpath, chunks, symbols, skeleton, True, total)

    # ---- frontmatter

    def _frontmatter(self, lines) -> tuple[int, str | None]:
        """Leading ---/+++ block. Returns (last_line_1based, title_or_None)."""
        first = lines[0].strip()
        fence = "---" if first == "---" else ("+++" if first == "+++" else None)
        if fence is None:
            return 0, None
        title = None
        for i in range(1, len(lines)):
            body = lines[i].strip()
            m = re.match(r"""title\s*[:=]\s*['"]?(.+?)['"]?\s*$""", body)
            if m and title is None:
                title = m.group(1)
            if body == fence:
                return i + 1, title
        return 0, None  # unterminated: treat as normal content

    # ---- scoring scan (one pass: fence state, headings, break scores)

    def _scan(self, lines, total, fm_end):
        heading: list[tuple[int, str] | None] = [None] * (total + 1)
        score = [1] * (total + 2)        # score for starting a NEW chunk at line i
        forbidden = [False] * (total + 2)
        in_fence = False
        prev_blank = True
        for i in range(1, total + 1):
            raw = lines[i - 1]
            stripped = raw.strip()
            is_fence = bool(_FENCE.match(raw))
            if i <= fm_end:
                forbidden[i] = True
                prev_blank = False
                continue
            if in_fence:
                forbidden[i] = True       # never cut inside a code block
                if is_fence:
                    in_fence = False
                prev_blank = False
                continue
            if is_fence:
                in_fence = True
                score[i] = 80             # break before a fenced code block
                prev_blank = False
                continue
            m = _HEADING.match(raw)
            if m:
                level = len(m.group(1))
                heading[i] = (level, m.group(2).strip())
                score[i] = 110 - level * 10   # H1=100 .. H6=50
                prev_blank = False
                continue
            if stripped == "":
                score[i] = 5
                prev_blank = True
                continue
            score[i] = 20 if prev_blank else 1   # paragraph start after a blank
            prev_blank = False
        return heading, score, forbidden

    # ---- cut selection (greedy, best-scoring break near the budget)

    def _cut(self, lines, total, score, forbidden):
        return qmd_cut(lines, 1, total, score, forbidden, self.budget)

    # ---- breadcrumb / heading stack

    def _stack_at(self, heading, s) -> list[tuple[int, str]]:
        """Heading stack active at (and including) line s."""
        stack: list[tuple[int, str]] = []
        for i in range(1, s + 1):
            h = heading[i]
            if h:
                level, text = h
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, text))
        return stack

    def _crumb(self, base, title, stack) -> str:
        parts = [base]
        if title and not stack:
            parts.append(title)
        parts += [f"{'#' * lvl} {txt}" for lvl, txt in stack]
        return " > ".join(parts)

    # ---- symbols & skeleton (the heading outline)

    def _symbols(self, relpath, heading, total) -> list[Symbol]:
        hlines = [i for i in range(1, total + 1) if heading[i]]
        out = []
        for idx, i in enumerate(hlines):
            level, text = heading[i]
            end = total
            for j in hlines[idx + 1:]:
                if heading[j][0] <= level:
                    end = j - 1
                    break
            stack = self._stack_at(heading, i)
            qualified = " > ".join(t for _, t in stack)
            out.append(Symbol(relpath, qualified, "heading", i, end,
                              f"{'#' * level} {text}"))
        return out

    def _skeleton(self, relpath, heading, total) -> str:
        parts = [f"# {relpath}"]
        for i in range(1, total + 1):
            h = heading[i]
            if h:
                level, text = h
                parts.append(f"{'#' * level} {text}")
        return "\n".join(parts)
