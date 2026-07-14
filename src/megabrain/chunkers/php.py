"""Legacy-PHP chunker — early-2000s procedural PHP where HTML, SQL and top-level
code share one file (no namespaces, no PSR: think `main.php` with 2000 lines of
statements, banner-comment sections, and `?> <html> … <?php` islands).

The generic tree-sitter cAST chunker treats such a file as one long run of
top-level statements and merges it into a handful of giant budget-sized chunks,
losing the file's real structure. This chunker recovers it:

- Top-level function/class defs become STANDALONE chunks (never merged with
  surrounding page code), with their preceding doc-comment run attached.
- Everything between defs ("flow": statements, comments, HTML islands) merges
  into sections; banner comments (`//------`) act like markdown headings — they
  name the section and are preferred cut points (same QMD scored-break selection
  as the markdown chunker, via the shared `qmd_cut`).
- HTML-only sections get kind "html"; named sections surface as kind "heading"
  symbols so outlines show the file's section map.

Routing lives in `PhpChunker` (the strategy-facing dispatcher): files with a
`namespace` declaration — or class-per-file OOP with no top-level soup — keep
the existing tree-sitter chunker byte-for-byte; only legacy-shaped files take
the section chunker. Same guarantees as every chunker: exact line partition,
breadcrumbs, symbols, skeleton (FileResult contract unchanged).
"""

from __future__ import annotations

import re

from .base import DEFAULT_BUDGET, Chunk, FileResult, Symbol, nws
from .markdown import qmd_cut
from .treesitter import (
    PHP_SPEC,
    TreeChunkerOps,
    TreeSitterChunker,
    first_line_signature,
    parser_for,
)

BANNER_MIN_FLUSH = 400   # a banner starts a new section only once the current
                         # one holds this much code (avoids dust sections)

# top-level defs that must stand alone as chunks (namespace/const stay "flow":
# consts are one-liners and legacy files have no namespaces by definition)
_PROTECTED = frozenset({"function_definition", "class_declaration",
                        "interface_declaration", "trait_declaration",
                        "enum_declaration"})

# `//------`, `#=====`, `/*****`, unicode box rules — the 2000s section divider
_BANNER = re.compile(r"^\s*(?://|#|/\*+)\s*[-=*#~_─═]{8,}")
# a wordy comment line usable as a section title (`// INCLUDES`, `// [0] …`)
_TITLE = re.compile(r"^\s*(?://|#|\*)\s*([A-Za-z0-9(\[].{2,70}?)\s*(?:\*/)?\s*$")


def looks_legacy(root) -> bool:
    """Heuristic on the parse tree's top level: legacy = procedural/mixed-HTML.
    A `namespace` declaration always means modern (PSR-era) — never rechunk it.
    Otherwise: top-level function defs, a statement soup, or interleaved HTML
    (`text_interpolation` islands) mark the file as legacy-shaped."""
    fn = flow = ti = 0
    for ch in root.named_children:
        t = ch.type
        if t == "namespace_definition":
            return False
        if t == "function_definition":
            fn += 1
        elif t in ("text_interpolation", "text"):
            ti += 1
            flow += 1
        elif t not in PHP_SPEC.def_types and t not in ("comment", "php_tag"):
            flow += 1
    return fn >= 1 or flow >= 3 or ti >= 2


class LegacyPhpChunker:
    def __init__(self, budget: int = DEFAULT_BUDGET, repo: str = ""):
        self.budget = budget
        self.repo = repo
        # composition: reuse the generic chunker's segmentation, naming,
        # oversized-def splitting and symbol/skeleton extraction — one source
        # of truth for everything that isn't legacy-specific. We depend on the
        # public TreeChunkerOps contract, never on chunker internals.
        self._ts: TreeChunkerOps = TreeSitterChunker(PHP_SPEC, budget=budget, repo=repo)

    # ---- public API

    def chunk_file(self, relpath: str, source: str, root=None) -> FileResult:
        lines = source.splitlines(keepends=True)
        total = len(lines)
        crumb = f"{self.repo} > {relpath}" if self.repo else relpath
        if total == 0:
            return FileResult(relpath, [], [], "", True, 0)
        src = source.encode()
        if root is None:
            try:
                root = parser_for(PHP_SPEC, "php").parse(src).root_node
            except Exception:
                root = None
        if root is None or not list(root.named_children):
            return FileResult(relpath,
                              self._ts.lines_fallback(relpath, lines, f"{crumb} (unparsed)"),
                              [], "", False, total)

        units = self._ts.segment(list(root.named_children), 1, total)
        if not units:
            c = Chunk(relpath, "module", None, 1, total, "".join(lines), crumb).finalize()
            return FileResult(relpath, [c], [], "", True, total)

        banner = self._banner_lines(lines, total)
        chunks, sections = self._build(units, lines, relpath, crumb, src, banner)
        symbols = self._ts.symbols_of(relpath, root, src)
        symbols += [Symbol(relpath, title, "heading", s, e, f"// {title}")
                    for title, s, e in sections]
        symbols.sort(key=lambda s: s.line)
        skeleton = self._skeleton(relpath, symbols)
        return FileResult(relpath, chunks, symbols, skeleton, True, total)

    # ---- banners / titles

    def _banner_lines(self, lines, total) -> list[bool]:
        b = [False] * (total + 2)
        for i in range(1, total + 1):
            if _BANNER.match(lines[i - 1]):
                b[i] = True
        return b

    def _section_title(self, lines, s, e) -> str | None:
        """`//----` + `// TITLE` at the section start names the section."""
        for j in range(s, min(s + 6, e + 1)):
            if _BANNER.match(lines[j - 1]) and j + 1 <= e and not _BANNER.match(lines[j]):
                m = _TITLE.match(lines[j])
                if m:
                    return m.group(1).strip()
        return None

    # ---- chunk construction

    def _build(self, units, lines, relpath, crumb, src, banner):
        chunks: list[Chunk] = []
        sections: list[tuple[str, int, int]] = []   # (title, start, end)
        buf: list[tuple] = []                       # pending flow units
        bsize = 0

        def flush_flow():
            nonlocal buf, bsize
            if not buf:
                return
            s, e = buf[0][1], buf[-1][2]
            for c, title in self._flow_chunks(buf, s, e, lines, relpath, crumb, banner):
                chunks.append(c)
                if title:
                    sections.append((title, c.start_line, c.end_line))
            buf, bsize = [], 0

        for u in units:
            n, s, e = u
            if n.type in _PROTECTED:
                # doc attachment: the trailing contiguous comment-unit run in the
                # buffer is this def's doc header — move it into the def's chunk.
                doc_start = s
                while buf and buf[-1][0].type == "comment":
                    doc_start = buf[-1][1]
                    bsize -= self._usize(lines, buf[-1][1], buf[-1][2])
                    buf.pop()
                flush_flow()
                chunks.extend(self._def_chunks(n, doc_start, e, lines, relpath, crumb, src))
            else:
                usz = self._usize(lines, s, e)
                # a banner comment starts a new section once the current one is
                # real — but ONLY when it follows code: a banner right after
                # other comments is the CLOSING rule of a doc header (`//----`
                # under a title), which must stay glued so the def below can
                # attach the whole header as its doc.
                if (buf and n.type == "comment" and banner[n.start_point[0] + 1]
                        and buf[-1][0].type != "comment"
                        and bsize >= BANNER_MIN_FLUSH):
                    flush_flow()
                buf.append(u)
                bsize += usz
        flush_flow()
        return chunks, sections

    def _usize(self, lines, s, e) -> int:
        return nws("".join(lines[s - 1:e]))

    def _def_chunks(self, node, s, e, lines, relpath, crumb, src) -> list[Chunk]:
        if self._usize(lines, s, e) > self.budget:
            # oversized function/class: the generic chunker's split (class ->
            # methods, function -> part k/n blocks) is exactly right — reuse it.
            return self._ts.split_unit((node, s, e), lines, relpath, crumb, src)
        kind = PHP_SPEC.def_types[node.type]
        name = self._ts.name_of(node)
        bc = f"{crumb} > {first_line_signature(node, src)}"
        return [Chunk(relpath, kind, name, s, e,
                      "".join(lines[s - 1:e]), bc).finalize()]

    def _flow_chunks(self, units, s, e, lines, relpath, crumb, banner):
        """One flow section -> [(chunk, title_or_None)]. Oversized sections cut
        at the best-scoring boundaries (banners / HTML transitions / unit
        starts) via the shared QMD break selection."""
        total = e
        if self._usize(lines, s, e) <= self.budget:
            ranges = [(s, e)]
        else:
            score = [1] * (total + 2)
            forbidden = [False] * (total + 2)
            for i in range(s, e + 1):
                if banner[i]:
                    score[i] = 85
                elif not lines[i - 1].strip():
                    score[i] = 5
            for n, us, _ue in units:
                start = us
                score[start] = max(score[start],
                                   90 if n.type in ("text_interpolation", "text") else 20)
            ranges = qmd_cut(lines, s, e, score, forbidden, self.budget)

        html_spans = [(us, ue) for n, us, ue in units
                      if n.type in ("text_interpolation", "text")]

        out = []
        for cs, ce in ranges:
            title = self._section_title(lines, cs, ce)
            html_nws = sum(self._usize(lines, max(us, cs), min(ue, ce))
                           for us, ue in html_spans if us <= ce and ue >= cs)
            sect_nws = self._usize(lines, cs, ce) or 1
            kind = "html" if html_nws / sect_nws > 0.6 else "module"
            if title:
                bc = f"{crumb} > [{title}]"
            else:
                bc = f"{crumb} ({'html' if kind == 'html' else 'module level'})"
            out.append((Chunk(relpath, kind, title, cs, ce,
                              "".join(lines[cs - 1:ce]), bc).finalize(), title))
        return out

    # ---- skeleton (section map + signatures, for the file-level embedding)

    def _skeleton(self, relpath, symbols) -> str:
        parts = [f"# {relpath}"]
        for s in symbols:
            if s.kind == "heading":
                parts.append(f"// {s.name}")
            else:
                indent = "    " if "." in s.name else ""
                parts.append(f"{indent}{s.signature}")
        return "\n".join(parts)


class PhpChunker:
    """Strategy-facing dispatcher: parse once, route by shape. Modern (PSR /
    namespaced / class-per-file) PHP keeps the generic tree-sitter chunker
    unchanged; legacy-shaped files take the section chunker."""

    def __init__(self, budget: int = DEFAULT_BUDGET, repo: str = ""):
        self._modern = TreeSitterChunker(PHP_SPEC, budget=budget, repo=repo)
        self._legacy = LegacyPhpChunker(budget=budget, repo=repo)

    def chunk_file(self, relpath: str, source: str) -> FileResult:
        try:
            root = parser_for(PHP_SPEC, "php").parse(source.encode()).root_node
        except Exception:
            root = None
        if root is not None and looks_legacy(root):
            return self._legacy.chunk_file(relpath, source, root=root)
        return self._modern.chunk_file(relpath, source)
