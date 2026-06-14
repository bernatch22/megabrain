"""cAST chunker for Python — split-then-merge over the AST (cAST, arXiv 2506.15655).

Guarantees:
- Chunks are a partition of the file's lines: no gaps, no overlaps, full coverage.
- Each chunk carries a breadcrumb (repo > path > Class > def method(sig)).
- Budget is measured in non-whitespace characters (default 4000).
- Oversized classes split into class_header + method chunks; oversized
  functions split into sequential block chunks (part k/n).
- Files that fail to parse fall back to whole-file chunks (split by budget).

Also extracts a symbol table and a file skeleton (signatures + docstrings).
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field, asdict

DEFAULT_BUDGET = 4000  # non-whitespace chars per chunk


def nws(text: str) -> int:
    return sum(1 for c in text if not c.isspace())


# ---------------------------------------------------------------- data model


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
    id: str = ""

    def finalize(self) -> "Chunk":
        self.nws_chars = nws(self.text)
        h = hashlib.sha1(f"{self.file}:{self.start_line}-{self.end_line}".encode()).hexdigest()[:12]
        self.id = h
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


# ---------------------------------------------------------------- helpers


def _node_start(node: ast.stmt) -> int:
    """Start line including decorators."""
    decos = getattr(node, "decorator_list", None)
    if decos:
        return decos[0].lineno
    return node.lineno


def _signature(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        try:
            args = ast.unparse(node.args)
        except Exception:
            args = "..."
        ret = ""
        if node.returns is not None:
            try:
                ret = f" -> {ast.unparse(node.returns)}"
            except Exception:
                pass
        return f"{prefix} {node.name}({args}){ret}"
    if isinstance(node, ast.ClassDef):
        bases = []
        for b in node.bases:
            try:
                bases.append(ast.unparse(b))
            except Exception:
                bases.append("?")
        return f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
    return ""


def _docline(node: ast.AST) -> str | None:
    try:
        doc = ast.get_docstring(node)
    except TypeError:
        return None
    if doc:
        lines = doc.strip().splitlines()
        return lines[0] if lines else None
    return None


@dataclass
class _Unit:
    """A top-level segment: an AST node plus the gap (comments/blanks) before it."""
    node: ast.stmt | None
    start: int  # 1-based, includes preceding gap
    end: int    # 1-based inclusive
    size: int = 0


def _segment(body: list[ast.stmt], region_start: int, region_end: int,
             lines: list[str]) -> list[_Unit]:
    """Partition [region_start, region_end] into units, one per top-level stmt.
    Gap lines (comments/blanks) before a stmt belong to that stmt's unit;
    trailing lines after the last stmt extend the last unit."""
    units: list[_Unit] = []
    cursor = region_start
    for node in body:
        start = min(_node_start(node), node.lineno)
        ustart = cursor
        uend = node.end_lineno or node.lineno
        # the unit starts where the previous ended; the node's own start may be
        # later (gap absorbed) but never earlier than cursor
        if start < cursor:
            # overlapping spans (e.g. same-line statements via semicolons) —
            # extend the previous unit instead
            if units and uend > units[-1].end:
                units[-1].end = uend
                cursor = uend + 1
                continue
            elif units:
                continue
        units.append(_Unit(node=node, start=ustart, end=uend))
        cursor = uend + 1
    if units and cursor <= region_end:
        units[-1].end = region_end
    for u in units:
        u.size = nws("".join(lines[u.start - 1:u.end]))
    return units


def _text(lines: list[str], start: int, end: int) -> str:
    return "".join(lines[start - 1:end])


# ---------------------------------------------------------------- chunker


class CastChunker:
    def __init__(self, budget: int = DEFAULT_BUDGET, repo: str = ""):
        self.budget = budget
        self.repo = repo

    # -- public API

    def chunk_file(self, relpath: str, source: str) -> FileResult:
        lines = source.splitlines(keepends=True)
        if not lines:
            return FileResult(relpath, [], [], "", True, 0)
        total = len(lines)
        crumb_base = f"{self.repo} > {relpath}" if self.repo else relpath
        try:
            tree = ast.parse(source)
        except SyntaxError:
            chunks = self._fallback_chunks(relpath, lines, crumb_base)
            return FileResult(relpath, chunks, [], "", False, total)

        units = _segment(tree.body, 1, total, lines)
        if not units:  # comment/blank-only file
            c = Chunk(relpath, "module", None, 1, total,
                      _text(lines, 1, total), crumb_base).finalize()
            return FileResult(relpath, [c], [], "", True, total)

        chunks = self._merge_units(units, lines, relpath, crumb_base, parent=None)
        symbols = self._extract_symbols(relpath, tree)
        skeleton = self._skeleton(relpath, tree)
        return FileResult(relpath, chunks, symbols, skeleton, True, total)

    # -- merging

    def _merge_units(self, units: list[_Unit], lines: list[str], relpath: str,
                     crumb_base: str, parent: ast.ClassDef | None) -> list[Chunk]:
        chunks: list[Chunk] = []
        buf: list[_Unit] = []
        buf_size = 0

        def flush():
            nonlocal buf, buf_size
            if not buf:
                return
            start, end = buf[0].start, buf[-1].end
            kind, name, crumb = self._describe(buf, crumb_base, parent)
            chunks.append(Chunk(relpath, kind, name, start, end,
                                _text(lines, start, end), crumb).finalize())
            buf, buf_size = [], 0

        for u in units:
            if u.size > self.budget:
                flush()
                chunks.extend(self._split_unit(u, lines, relpath, crumb_base, parent))
            elif buf_size + u.size > self.budget:
                flush()
                buf, buf_size = [u], u.size
            else:
                buf.append(u)
                buf_size += u.size
        flush()
        return chunks

    def _describe(self, buf: list[_Unit], crumb_base: str,
                  parent: ast.ClassDef | None) -> tuple[str, str | None, str]:
        """kind/name/breadcrumb for a merged chunk."""
        named = [u for u in buf
                 if isinstance(u.node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        prefix = f"{parent.name}." if parent else ""
        if len(named) == 1:
            node = named[0].node
            if isinstance(node, ast.ClassDef):
                kind = "class"
            elif parent:
                kind = "method"
            else:
                kind = "function"
            name = f"{prefix}{node.name}"
            crumb = f"{crumb_base} > {_signature(node)}"
            if parent:
                crumb = f"{crumb_base} > {_signature(parent)} > {_signature(node)}"
            return kind, name, crumb
        if named:
            names = ", ".join(f"{prefix}{u.node.name}" for u in named)
            crumb = f"{crumb_base} > [{names}]"
            if parent:
                crumb = f"{crumb_base} > {_signature(parent)} > [{names}]"
            return ("method" if parent else "module"), names, crumb
        # constants / imports / bare statements
        if parent:
            return "class_header", f"{parent.name}", f"{crumb_base} > {_signature(parent)} (class body)"
        return "module", None, f"{crumb_base} (module level)"

    def _split_unit(self, u: _Unit, lines: list[str], relpath: str,
                    crumb_base: str, parent: ast.ClassDef | None) -> list[Chunk]:
        node = u.node
        if isinstance(node, ast.ClassDef):
            return self._split_class(u, lines, relpath, crumb_base)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return self._split_function(u, lines, relpath, crumb_base, parent)
        # giant non-def statement (huge dict literal, etc.) — split by lines
        return self._split_lines(u.start, u.end, lines, relpath,
                                 f"{crumb_base} (module level)", "module")

    def _split_class(self, u: _Unit, lines: list[str], relpath: str,
                     crumb_base: str) -> list[Chunk]:
        cls: ast.ClassDef = u.node  # type: ignore[assignment]
        body_units = _segment(cls.body, u.start, u.end, lines)
        # everything before the first stmt unit (decorators, class line) is
        # absorbed into the first unit by _segment(region_start=u.start)
        return self._merge_units(body_units, lines, relpath, crumb_base, parent=cls)

    def _split_function(self, u: _Unit, lines: list[str], relpath: str,
                        crumb_base: str, parent: ast.ClassDef | None) -> list[Chunk]:
        fn = u.node  # type: ignore[assignment]
        prefix = f"{parent.name}." if parent else ""
        name = f"{prefix}{fn.name}"
        crumb = f"{crumb_base} > {_signature(parent)} > {_signature(fn)}" if parent \
            else f"{crumb_base} > {_signature(fn)}"
        body_units = _segment(fn.body, u.start, u.end, lines)
        # greedy block packing
        blocks: list[tuple[int, int]] = []
        bstart, bsize = None, 0
        bend = None
        for bu in body_units:
            if bu.size > self.budget:
                if bstart is not None:
                    blocks.append((bstart, bend))
                    bstart, bsize = None, 0
                # giant single statement inside a function: line-split
                blocks.extend(self._pack_lines(bu.start, bu.end, lines))
                continue
            if bstart is None:
                bstart, bend, bsize = bu.start, bu.end, bu.size
            elif bsize + bu.size > self.budget:
                blocks.append((bstart, bend))
                bstart, bend, bsize = bu.start, bu.end, bu.size
            else:
                bend, bsize = bu.end, bsize + bu.size
        if bstart is not None:
            blocks.append((bstart, bend))
        n = len(blocks)
        kind = "method" if parent else "function"
        out = []
        for i, (s, e) in enumerate(blocks, 1):
            part = f"{i}/{n}" if n > 1 else None
            out.append(Chunk(relpath, kind if n == 1 else "block", name, s, e,
                             _text(lines, s, e), crumb, part=part).finalize())
        return out

    def _pack_lines(self, start: int, end: int, lines: list[str]) -> list[tuple[int, int]]:
        """Split [start, end] into line-windows each within budget."""
        out = []
        s = start
        size = 0
        for ln in range(start, end + 1):
            lsize = nws(lines[ln - 1])
            if size + lsize > self.budget and ln > s:
                out.append((s, ln - 1))
                s, size = ln, lsize
            else:
                size += lsize
        out.append((s, end))
        return out

    def _split_lines(self, start: int, end: int, lines: list[str], relpath: str,
                     crumb: str, kind: str) -> list[Chunk]:
        wins = self._pack_lines(start, end, lines)
        n = len(wins)
        return [Chunk(relpath, kind if n == 1 else "block", None, s, e,
                      _text(lines, s, e), crumb,
                      part=(f"{i}/{n}" if n > 1 else None)).finalize()
                for i, (s, e) in enumerate(wins, 1)]

    def _fallback_chunks(self, relpath: str, lines: list[str], crumb: str) -> list[Chunk]:
        return self._split_lines(1, len(lines), lines, relpath,
                                 f"{crumb} (unparsed)", "file")

    # -- symbols

    def _extract_symbols(self, relpath: str, tree: ast.Module) -> list[Symbol]:
        out: list[Symbol] = []

        def deco_names(node) -> list[str]:
            names = []
            for d in getattr(node, "decorator_list", []):
                try:
                    names.append(ast.unparse(d))
                except Exception:
                    names.append("?")
            return names

        def visit(body: list[ast.stmt], prefix: str, in_class: bool):
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    base = "method" if in_class else "function"
                    kind = f"async_{base}" if isinstance(node, ast.AsyncFunctionDef) else base
                    out.append(Symbol(relpath, f"{prefix}{node.name}", kind,
                                      _node_start(node), node.end_lineno or node.lineno,
                                      _signature(node), deco_names(node), _docline(node)))
                elif isinstance(node, ast.ClassDef):
                    out.append(Symbol(relpath, f"{prefix}{node.name}", "class",
                                      _node_start(node), node.end_lineno or node.lineno,
                                      _signature(node), deco_names(node), _docline(node)))
                    visit(node.body, f"{prefix}{node.name}.", True)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for t in targets:
                        if isinstance(t, ast.Name):
                            kind = "class_attr" if in_class else "constant"
                            try:
                                sig = ast.unparse(node).splitlines()[0][:120]
                            except Exception:
                                sig = t.id
                            out.append(Symbol(relpath, f"{prefix}{t.id}", kind,
                                              node.lineno, node.end_lineno or node.lineno,
                                              sig))

        visit(tree.body, "", False)
        return out

    # -- skeleton

    def _skeleton(self, relpath: str, tree: ast.Module) -> str:
        """Compact file map: docstring, constants, signatures — for file-level embedding."""
        parts: list[str] = [f"# {relpath}"]
        doc = _docline(tree)
        if doc:
            parts.append(f'"""{doc}"""')

        def emit(body: list[ast.stmt], indent: str):
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    line = f"{indent}{_signature(node)}"
                    d = _docline(node)
                    parts.append(line + (f"  # {d}" if d else ""))
                elif isinstance(node, ast.ClassDef):
                    d = _docline(node)
                    parts.append(f"{indent}{_signature(node)}" + (f"  # {d}" if d else ""))
                    emit(node.body, indent + "    ")
                elif isinstance(node, (ast.Assign, ast.AnnAssign)) and indent == "":
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for t in targets:
                        if isinstance(t, ast.Name) and (t.id.isupper() or t.id.startswith("_")):
                            try:
                                parts.append(ast.unparse(node).splitlines()[0][:120])
                            except Exception:
                                parts.append(t.id)

        emit(tree.body, "")
        return "\n".join(parts)


# ---------------------------------------------------------------- validation


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
