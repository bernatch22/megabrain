"""Shared chunking contract — every chunker (Python, tree-sitter, markdown,
legacy-PHP) emits this same data model and guarantees:

- Chunks are a partition of the file's lines: no gaps, no overlaps, full
  coverage (validate_partition checks it — a hard engine invariant).
- Each chunk carries a breadcrumb (repo > path > Class > def method(sig)).
- Budget is measured in non-whitespace characters (default 4000).
- Symbols and a file skeleton (signatures/docstrings or heading outline)
  come along in FileResult.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

DEFAULT_BUDGET = 4000  # non-whitespace chars per chunk


def nws(text: str) -> int:
    return sum(1 for c in text if not c.isspace())


@dataclass
class Chunk:
    file: str
    kind: str          # module | class_header | class | function | method | block | file
    name: str | None   # qualified name, e.g. "Service.handle"
    start_line: int    # 1-based inclusive
    end_line: int      # 1-based inclusive
    text: str
    breadcrumb: str
    part: str | None = None  # "2/5" for split-function blocks
    nws_chars: int = 0

    def finalize(self) -> "Chunk":
        self.nws_chars = nws(self.text)
        return self

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Symbol:
    file: str
    name: str          # qualified: "Service.handle", "MAX_RETRIES"
    kind: str          # function | async_function | class | method | async_method | constant | class_attr
    line: int
    end_line: int
    signature: str
    decorators: list[str] = field(default_factory=list)
    doc: str | None = None  # first line of docstring

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FileResult:
    file: str
    chunks: list[Chunk]
    symbols: list[Symbol]
    skeleton: str
    parse_ok: bool
    total_lines: int


def validate_partition(result: FileResult) -> list[str]:
    """Return list of violations (empty = perfect partition)."""
    errs = []
    chunks = sorted(result.chunks, key=lambda c: c.start_line)
    if not chunks:
        if result.total_lines > 0:
            errs.append("no chunks for non-empty file")
        return errs
    if chunks[0].start_line != 1:
        errs.append(f"first chunk starts at {chunks[0].start_line}, not 1")
    for a, b in zip(chunks, chunks[1:]):
        if b.start_line != a.end_line + 1:
            errs.append(f"gap/overlap between L{a.end_line} and L{b.start_line}")
    if chunks[-1].end_line != result.total_lines:
        errs.append(f"last chunk ends at {chunks[-1].end_line}, file has {result.total_lines}")
    return errs


def embed_text(chunk: Chunk) -> str:
    """Text to embed: breadcrumb header + raw code (contextual retrieval)."""
    header = f"# {chunk.breadcrumb}"
    if chunk.part:
        header += f" (part {chunk.part})"
    return f"{header}\n{chunk.text}"
