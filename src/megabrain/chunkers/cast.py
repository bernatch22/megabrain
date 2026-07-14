"""cast — THE cAST split-then-merge engine (arXiv 2506.15655), one copy.

Both structural chunkers — CastChunker (Python `ast`) and TreeSitterChunker
(tree_sitter, all other languages) — ran their own byte-identical copy of the
same three primitives: the greedy MERGE of small sibling units up to the
budget, the greedy PACK of an oversized def's body into k/n blocks, and the
budget-sized LINE-WINDOW fallback. They now share these, parameterized only by
how each represents a "unit" (an `ast` node vs a tree-sitter node tuple):

    merge_units   split-then-merge driver: pack small units, delegate the
                  oversized ones to a splitter (the caller supplies size_of,
                  emit_merged, emit_split — everything language-specific).
    greedy_pack   pack (start, end, size) triples into <=budget line blocks;
                  an oversized triple is line-windowed via pack_lines.
    pack_lines    split [start, end] into windows each within budget (nws).

DEFAULT_BUDGET (4000 non-whitespace chars) is the measured optimum — it beat
five alternatives on the golden set; do not retune casually. Byte-identity with
the pre-unification engine is proven by tests/test_cast_unification.py over the
engine's own Python corpus + tree-sitter samples.
"""

from __future__ import annotations

from typing import Callable

from .base import nws


def pack_lines(lines: list[str], start: int, end: int,
               budget: int) -> list[tuple[int, int]]:
    """Split the inclusive line range [start, end] into windows each within
    `budget` non-whitespace chars (a single over-budget line stands alone).
    The last-resort partition when there is no structure to exploit."""
    out: list[tuple[int, int]] = []
    s = start
    size = 0
    for ln in range(start, end + 1):
        lsize = nws(lines[ln - 1])
        if size + lsize > budget and ln > s:
            out.append((s, ln - 1))
            s, size = ln, lsize
        else:
            size += lsize
    out.append((s, end))
    return out


def greedy_pack(triples: list[tuple[int, int, int]], lines: list[str],
                budget: int) -> list[tuple[int, int]]:
    """Greedily pack (start, end, size) units into <=budget line blocks, in
    order. An oversized unit flushes the current block and is line-windowed
    (pack_lines). Returns the block (start, end) ranges — the k/n split of an
    oversized def's body."""
    blocks: list[tuple[int, int]] = []
    bstart: int | None = None
    bend: int | None = None
    bsize = 0
    for s, e, size in triples:
        if size > budget:
            if bstart is not None:
                blocks.append((bstart, bend))  # type: ignore[arg-type]
                bstart, bend, bsize = None, None, 0
            blocks.extend(pack_lines(lines, s, e, budget))
        elif bstart is None:
            bstart, bend, bsize = s, e, size
        elif bsize + size > budget:
            blocks.append((bstart, bend))  # type: ignore[arg-type]
            bstart, bend, bsize = s, e, size
        else:
            bend, bsize = e, bsize + size
    if bstart is not None:
        blocks.append((bstart, bend))  # type: ignore[arg-type]
    return blocks


def merge_units(units: list, size_of: Callable[[object], int], budget: int,
                emit_merged: Callable[[list], list],
                emit_split: Callable[[object], list]) -> list:
    """Split-then-merge driver. Walk `units` in order, buffering small ones
    until the buffer would exceed `budget` (then `emit_merged(buf)` seals a
    chunk run); an over-budget unit seals the buffer and is handed to
    `emit_split(u)`. `size_of`, `emit_merged`, `emit_split` carry all the
    language-specific behavior — this loop is the shared structure."""
    chunks: list = []
    buf: list = []
    bsize = 0

    def flush():
        nonlocal buf, bsize
        if buf:
            chunks.extend(emit_merged(buf))
            buf, bsize = [], 0

    for u in units:
        usz = size_of(u)
        if usz > budget:
            flush()
            chunks.extend(emit_split(u))
        elif bsize + usz > budget:
            flush()
            buf, bsize = [u], usz
        else:
            buf.append(u)
            bsize += usz
    flush()
    return chunks
