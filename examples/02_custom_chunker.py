"""Teach megabrain a new content type — a custom `.sql` chunker, no fork needed.

    python examples/02_custom_chunker.py          # chunking part: fully OFFLINE

A chunking strategy is any object with:
    exts                                     extensions it claims
    chunk_file(relpath, source) -> FileResult    chunks + symbols + skeleton
    build_edge_ctx / extract_edges           dependency-graph hooks (None = no graph)

The ONE hard rule: a file's chunks must be an exact line partition — no gaps,
no overlaps, full coverage. `validate_partition` checks it; the engine's own
chunkers are all held to the same invariant.

Pass instances via `index_repo(root, strategies=[SqlStrategy()])`. Custom
strategies are matched BEFORE the built-ins, so you can claim a new extension
or override how an existing one is chunked.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

from megabrain import Chunk, FileResult, Symbol, validate_partition
from megabrain.chunkers import nws

BUDGET = 4000  # non-whitespace chars per chunk — same notion as the engine

# `CREATE [OR REPLACE|UNIQUE|TEMP] TABLE|VIEW|INDEX|TRIGGER|FUNCTION name`
_CREATE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+|UNIQUE\s+|TEMP(?:ORARY)?\s+)*"
    r"(TABLE|VIEW|INDEX|TRIGGER|FUNCTION|PROCEDURE)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"[`\"]?(\w+)", re.I)


class SqlChunker:
    """Statement-aware SQL chunker.

    Units are `;`-terminated statements WITH their preceding comment run (the
    doc header travels with its statement). Small units merge greedily up to
    the budget; each named statement becomes a symbol; the skeleton is the
    list of statement headlines (what the file-level embedding sees)."""

    def __init__(self, budget: int = BUDGET, repo: str = ""):
        self.budget = budget
        self.repo = repo

    def chunk_file(self, relpath: str, source: str) -> FileResult:
        lines = source.splitlines(keepends=True)
        total = len(lines)
        crumb = f"{self.repo} > {relpath}" if self.repo else relpath
        if total == 0:
            return FileResult(relpath, [], [], "", True, 0)

        # 1. statement units: [start, end] line ranges, gaps attached forward
        units: list[tuple[int, int]] = []
        start = 1
        for i, ln in enumerate(lines, 1):
            if ln.split("--", 1)[0].rstrip().endswith(";") or i == total:
                units.append((start, i))
                start = i + 1

        # 2. name/kind + symbols per unit
        def describe(s: int, e: int) -> tuple[str, str | None]:
            m = _CREATE.search("".join(lines[s - 1:e]))
            return (m.group(1).lower(), m.group(2)) if m else ("statement", None)

        symbols = []
        for s, e in units:
            kind, name = describe(s, e)
            if name:
                head = next(ln for ln in lines[s - 1:e] if _CREATE.search(ln))
                symbols.append(Symbol(relpath, name, kind, s, e, head.strip().rstrip("(")))

        # 3. greedy merge to budget (production chunkers also line-split
        #    oversized single units — omitted here for clarity)
        chunks: list[Chunk] = []
        bs, be, size = None, None, 0

        def flush():
            nonlocal bs, be, size
            if bs is None:
                return
            names = [sy.name for sy in symbols if bs <= sy.line and sy.end_line <= be]
            kind = describe(bs, be)[0] if len(names) == 1 else "module"
            name = ", ".join(names) or None
            bc = f"{crumb} > [{name}]" if name else f"{crumb} (statements)"
            chunks.append(Chunk(relpath, kind, name, bs, be,
                                "".join(lines[bs - 1:be]), bc).finalize())
            bs, be, size = None, None, 0

        for s, e in units:
            usz = nws("".join(lines[s - 1:e]))
            if bs is not None and size + usz > self.budget:
                flush()
            if bs is None:
                bs = s
            be, size = e, size + usz
        flush()

        skeleton = "\n".join([f"# {relpath}", *(sy.signature for sy in symbols)])
        return FileResult(relpath, chunks, symbols, skeleton, True, total)


class SqlStrategy:
    """The engine-facing wrapper: extensions + chunker + (no) graph hooks."""

    exts = (".sql",)

    def __init__(self, repo: str = ""):
        self._chunker = SqlChunker(repo=repo)

    def chunk_file(self, relpath: str, source: str) -> FileResult:
        return self._chunker.chunk_file(relpath, source)

    def build_edge_ctx(self, sources: dict[str, str], repo_name: str):
        return None            # SQL has no import graph (edges are optional)

    def extract_edges(self, relpath: str, source: str, ctx):
        return None


def main():
    sample = Path(__file__).parent / "sample_sql" / "shop.sql"
    rel = "sample_sql/shop.sql"

    # ---- OFFLINE: chunk + inspect (this is 90% of writing a chunker) ------
    # tiny budget so the 55-line sample visibly splits; the engine default
    # (and SqlStrategy below) is 4000 nws chars, same as every built-in.
    r = SqlChunker(budget=600).chunk_file(rel, sample.read_text())
    violations = validate_partition(r)
    assert not violations, violations   # the invariant your chunker must hold

    print(f"{rel}: {r.total_lines} lines -> {len(r.chunks)} chunks, "
          f"{len(r.symbols)} symbols, partition OK\n")
    for c in r.chunks:
        print(f"  L{c.start_line:>3}-{c.end_line:<3} {c.kind:<9} "
              f"{c.name or '-':<40} {c.nws_chars} nws")
    print("\nskeleton (file-level embedding input):")
    print("  " + r.skeleton.replace("\n", "\n  "))

    # ---- ONLINE (needs OPENROUTER_API_KEY or a local endpoint): ----------
    from megabrain.providers import find_embed_key
    if not find_embed_key(required=False):
        print("\n(no embedding key found — set OPENROUTER_API_KEY to also "
              "index + search the sample)")
        return

    from megabrain import index_repo, render, search
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "shop"
        repo.mkdir()
        shutil.copy(sample, repo / "shop.sql")
        (repo / "report.py").write_text(
            'def top_customers(db):\n'
            '    """Rank customers by revenue (customer_revenue view)."""\n'
            '    return db.execute("SELECT * FROM customer_revenue '
            'ORDER BY revenue_cents DESC")\n')

        index_repo(repo, strategies=[SqlStrategy(repo="shop")])
        res = search(repo, "where does customer revenue come from")
        print("\n" + render(res, compact=True))


if __name__ == "__main__":
    sys.exit(main())
