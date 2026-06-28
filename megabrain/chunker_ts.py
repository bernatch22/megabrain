"""Tree-sitter cAST chunker — same algorithm and guarantees as the Python
chunker: split-then-merge over the AST, line-partition with no gaps or overlaps,
breadcrumbs, symbols, file skeleton.

The cAST split-then-merge + breadcrumb + partition logic is language-agnostic;
only node-type recognition changes. `TreeSitterChunker` runs the algorithm; a
`LangSpec` supplies the per-language config (grammar, def node types, name/body
fields, export-unwrapping). Adding a language = one `LangSpec` entry + the
`tree_sitter_<lang>` grammar — no new class.

  TS/TSX/JS/JSX  -> TS_SPEC   (TS grammar is a JS superset; .jsx -> tsx grammar)
  Ruby           -> RUBY_SPEC
  Go             -> GO_SPEC

`TsChunker` is kept as a thin alias (TS_SPEC) for back-compat with existing
imports and the gate tests — its output is byte-identical to before the refactor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from tree_sitter import Language, Parser

from .chunker import Chunk, FileResult, Symbol, nws

DEFAULT_BUDGET = 4000


# ---------------------------------------------------------------- language specs


@dataclass(frozen=True)
class LangSpec:
    """Per-language recognition config for the generic tree-sitter chunker."""
    name: str
    grammar: Callable[[str], object]      # ext -> tree-sitter language capsule (lazy import)
    def_types: dict[str, str]             # node type -> chunk/symbol kind
    container_types: frozenset[str] = frozenset()  # types whose body holds nested defs (classes)
    name_field: str = "name"              # field carrying the declared name
    extra_name_fields: tuple[str, ...] = ()  # fallback fields when `name` is absent
    #   (e.g. Rust `impl Foo` has no `name`; its target is in the `type` field)
    name_via: dict[str, tuple[str, str]] = field(default_factory=dict)
    # node types whose name is nested: type -> (child_type, child_name_field)
    body_field: str = "body"
    unwrap_exports: bool = False          # peel `export ...` wrappers (TS/JS only)


def _ts_grammar(ext: str):
    import tree_sitter_typescript as tst
    return tst.language_tsx() if ext in ("tsx", "jsx") else tst.language_typescript()


def _ruby_grammar(ext: str):
    import tree_sitter_ruby
    return tree_sitter_ruby.language()


def _go_grammar(ext: str):
    import tree_sitter_go
    return tree_sitter_go.language()


def _rust_grammar(ext: str):
    import tree_sitter_rust
    return tree_sitter_rust.language()


TS_SPEC = LangSpec(
    name="ts",
    grammar=_ts_grammar,
    def_types={
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "class_declaration": "class",
        "abstract_class_declaration": "class",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "enum",
        "method_definition": "method",
        "lexical_declaration": "const",
        "variable_declaration": "const",
    },
    container_types=frozenset({"class_declaration", "abstract_class_declaration"}),
    name_via={
        "lexical_declaration": ("variable_declarator", "name"),
        "variable_declaration": ("variable_declarator", "name"),
    },
    unwrap_exports=True,
)

RUBY_SPEC = LangSpec(
    name="ruby",
    grammar=_ruby_grammar,
    def_types={
        "method": "method",
        "singleton_method": "method",
        "class": "class",
        "module": "module",
    },
    container_types=frozenset({"class", "module"}),
)

GO_SPEC = LangSpec(
    name="go",
    grammar=_go_grammar,
    def_types={
        "function_declaration": "function",
        "method_declaration": "method",
        "type_declaration": "type",
        "const_declaration": "const",
        "var_declaration": "var",
    },
    name_via={
        "type_declaration": ("type_spec", "name"),
        "const_declaration": ("const_spec", "name"),
        "var_declaration": ("var_spec", "name"),
    },
)

RUST_SPEC = LangSpec(
    name="rust",
    grammar=_rust_grammar,
    def_types={
        "function_item": "function",
        "function_signature_item": "function",   # trait method decls (no body)
        "struct_item": "struct",
        "enum_item": "enum",
        "union_item": "union",
        "trait_item": "trait",
        "impl_item": "impl",
        "mod_item": "module",
        "type_item": "type",
        "const_item": "const",
        "static_item": "static",
        "macro_definition": "macro",
    },
    # impl/trait/mod bodies hold nested fns -> recurse so methods become symbols.
    container_types=frozenset({"impl_item", "trait_item", "mod_item"}),
    # `impl Foo`/`impl Trait for Foo` carry no `name`; fall back to the `type`
    # field (the implemented-on type). All other Rust defs resolve via `name`.
    extra_name_fields=("type",),
)


_parsers: dict[tuple[str, str], Parser] = {}


def _parser(spec: LangSpec, ext: str) -> Parser:
    key = (spec.name, ext)
    if key not in _parsers:
        _parsers[key] = Parser(Language(spec.grammar(ext)))
    return _parsers[key]


def _signature(node, source: bytes) -> str:
    """First line of the declaration, trimmed."""
    line = source[node.start_byte:node.end_byte].split(b"\n", 1)[0].decode(errors="replace")
    return line.strip().rstrip("{").strip()[:140]


# ---------------------------------------------------------------- chunker


class TreeSitterChunker:
    def __init__(self, spec: LangSpec, budget: int = DEFAULT_BUDGET, repo: str = ""):
        self.spec = spec
        self.budget = budget
        self.repo = repo

    # ---- language hooks (driven by the spec)

    def _name_of(self, node) -> str | None:
        n = node.child_by_field_name(self.spec.name_field)
        if n is not None:
            return n.text.decode()
        for fld in self.spec.extra_name_fields:
            n = node.child_by_field_name(fld)
            if n is not None:
                return n.text.decode()
        via = self.spec.name_via.get(node.type)
        if via:
            child_type, fname = via
            for ch in node.named_children:
                if ch.type == child_type:
                    nn = ch.child_by_field_name(fname)
                    if nn is not None:
                        return nn.text.decode()
        return None

    def _unwrap(self, node):
        """export_statement -> the declaration inside it (TS/JS only)."""
        if not self.spec.unwrap_exports:
            return node
        if node.type == "export_statement":
            d = node.child_by_field_name("declaration")
            if d is not None:
                return d
            for ch in node.named_children:
                if ch.type in self.spec.def_types:
                    return ch
        return node

    # ---- public API

    def chunk_file(self, relpath: str, source: str) -> FileResult:
        lines = source.splitlines(keepends=True)
        total = len(lines)
        crumb = f"{self.repo} > {relpath}" if self.repo else relpath
        if total == 0:
            return FileResult(relpath, [], [], "", True, 0)
        ext = relpath.rsplit(".", 1)[-1] if "." in relpath else ""
        try:
            tree = _parser(self.spec, ext).parse(source.encode())
        except Exception:
            return FileResult(relpath, self._lines_fallback(relpath, lines, crumb),
                              [], "", False, total)
        root = tree.root_node
        src = source.encode()
        units = self._segment(list(root.named_children), 1, total)
        if not units:
            c = Chunk(relpath, "module", None, 1, total,
                      "".join(lines), crumb).finalize()
            return FileResult(relpath, [c], [], "", True, total)
        chunks = self._merge(units, lines, relpath, crumb, parent=None, src=src)
        symbols = self._symbols(relpath, root, src)
        skeleton = self._skeleton(relpath, root, src)
        return FileResult(relpath, chunks, symbols, skeleton, True, total)

    # ---- segmentation (line partition, gaps attach to following node)

    def _segment(self, nodes, region_start: int, region_end: int):
        units = []
        cursor = region_start
        for n in nodes:
            s, e = n.start_point[0] + 1, n.end_point[0] + 1
            if e < cursor:
                continue
            units.append((n, cursor, e))
            cursor = e + 1
        if units:
            n, s, e = units[-1]
            units[-1] = (n, s, max(e, region_end))
        return units

    def _usize(self, lines, s, e):
        return nws("".join(lines[s - 1:e]))

    def _merge(self, units, lines, relpath, crumb, parent, src):
        chunks: list[Chunk] = []
        buf: list[tuple] = []
        bsize = 0

        def flush():
            nonlocal buf, bsize
            if not buf:
                return
            s, e = buf[0][1], buf[-1][2]
            kind, name, bc = self._describe(buf, crumb, parent, src)
            chunks.append(Chunk(relpath, kind, name, s, e,
                                "".join(lines[s - 1:e]), bc).finalize())
            buf, bsize = [], 0

        for u in units:
            usz = self._usize(lines, u[1], u[2])
            if usz > self.budget:
                flush()
                chunks.extend(self._split_unit(u, lines, relpath, crumb, src))
            elif bsize + usz > self.budget:
                flush()
                buf, bsize = [u], usz
            else:
                buf.append(u)
                bsize += usz
        flush()
        return chunks

    def _describe(self, buf, crumb, parent, src):
        named = []
        for n, s, e in buf:
            d = self._unwrap(n)
            if d.type in self.spec.def_types and self._name_of(d):
                named.append(d)
        prefix = ""
        pcrumb = crumb
        if parent is not None:
            prefix = f"{self._name_of(parent)}."
            pcrumb = f"{crumb} > {_signature(parent, src)}"
        if len(named) == 1:
            d = named[0]
            kind = self.spec.def_types[d.type]
            if parent is not None and kind == "function":
                kind = "method"
            return kind, f"{prefix}{self._name_of(d)}", f"{pcrumb} > {_signature(d, src)}"
        if named:
            names = ", ".join(f"{prefix}{self._name_of(d)}" for d in named)
            return ("method" if parent is not None else "module"), names, f"{pcrumb} > [{names}]"
        return ("class_header" if parent is not None else "module"), \
            (self._name_of(parent) if parent is not None else None), \
            f"{pcrumb} ({'class body' if parent is not None else 'module level'})"

    def _split_unit(self, u, lines, relpath, crumb, src):
        node, s, e = u
        d = self._unwrap(node)
        body = d.child_by_field_name(self.spec.body_field)
        if d.type in self.spec.container_types and body is not None:
            inner = self._segment(list(body.named_children), s, e)
            if inner:
                return self._merge(inner, lines, relpath, crumb, parent=d, src=src)
        if body is not None and body.named_children:
            name = self._name_of(d)
            bc = f"{crumb} > {_signature(d, src)}"
            inner = self._segment(list(body.named_children), s, e)
            if inner:
                blocks = self._pack(inner, lines)
                n = len(blocks)
                return [Chunk(relpath, "block" if n > 1 else self.spec.def_types.get(d.type, "block"),
                              name, bs, be, "".join(lines[bs - 1:be]), bc,
                              part=(f"{i}/{n}" if n > 1 else None)).finalize()
                        for i, (bs, be) in enumerate(blocks, 1)]
        # unsplittable big node: line windows
        return self._lines_fallback(relpath, lines, f"{crumb} > {_signature(d, src)}",
                                    start=s, end=e)

    def _pack(self, units, lines):
        blocks = []
        bs = be = None
        size = 0
        for n, s, e in units:
            usz = self._usize(lines, s, e)
            if usz > self.budget:
                # oversized single unit: flush, then line-window it
                if bs is not None:
                    blocks.append((bs, be))
                    bs, be, size = None, None, 0
                ws, wsize = s, 0
                for ln in range(s, e + 1):
                    lsz = nws(lines[ln - 1])
                    if wsize + lsz > self.budget and ln > ws:
                        blocks.append((ws, ln - 1))
                        ws, wsize = ln, lsz
                    else:
                        wsize += lsz
                blocks.append((ws, e))
            elif bs is None:
                bs, be, size = s, e, usz
            elif size + usz > self.budget:
                blocks.append((bs, be))
                bs, be, size = s, e, usz
            else:
                be, size = e, size + usz
        if bs is not None:
            blocks.append((bs, be))
        return blocks

    def _lines_fallback(self, relpath, lines, crumb, start=1, end=None):
        end = end or len(lines)
        out, s, size = [], start, 0
        wins = []
        for ln in range(start, end + 1):
            lsz = nws(lines[ln - 1])
            if size + lsz > self.budget and ln > s:
                wins.append((s, ln - 1))
                s, size = ln, lsz
            else:
                size += lsz
        wins.append((s, end))
        n = len(wins)
        for i, (a, b) in enumerate(wins, 1):
            out.append(Chunk(relpath, "block" if n > 1 else "file", None, a, b,
                             "".join(lines[a - 1:b]), crumb,
                             part=(f"{i}/{n}" if n > 1 else None)).finalize())
        return out

    # ---- symbols & skeleton

    def _symbols(self, relpath, root, src) -> list[Symbol]:
        out: list[Symbol] = []

        def visit(node, prefix):
            for ch in node.named_children:
                d = self._unwrap(ch)
                if d.type not in self.spec.def_types:
                    continue
                name = self._name_of(d)
                if not name:
                    continue
                kind = self.spec.def_types[d.type]
                out.append(Symbol(relpath, f"{prefix}{name}", kind,
                                  d.start_point[0] + 1, d.end_point[0] + 1,
                                  _signature(d, src)))
                if d.type in self.spec.container_types:
                    body = d.child_by_field_name(self.spec.body_field)
                    if body is not None:
                        visit(body, f"{prefix}{name}.")

        visit(root, "")
        return out

    def _skeleton(self, relpath, root, src) -> str:
        parts = [f"# {relpath}"]
        for s in self._symbols(relpath, root, src):
            indent = "    " if "." in s.name else ""
            parts.append(f"{indent}{s.signature}")
        return "\n".join(parts)


class TsChunker(TreeSitterChunker):
    """TypeScript/TSX/JS/JSX chunker (back-compat alias over TS_SPEC)."""

    def __init__(self, budget: int = DEFAULT_BUDGET, repo: str = ""):
        super().__init__(TS_SPEC, budget=budget, repo=repo)
