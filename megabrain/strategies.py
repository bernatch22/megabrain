"""Chunking-strategy registry: maps a file extension to a chunker + an optional
edge extractor, so adding a language or content type is a config entry rather
than a branch in the indexer. Every strategy emits the same content-agnostic
FileResult, so the embed/store/query/ask pipeline never changes.

A strategy (see the ChunkStrategy protocol) has three parts:
  - exts:            extensions it claims
  - chunk_file:      relpath, source -> FileResult   (the per-file chunker)
  - build_edge_ctx:  whole-repo prepass for the graph (or None if no graph)
  - extract_edges:   relpath, source, ctx -> [(dst, kind)] | None
                     None means "this content type has no graph" (e.g. docs) —
                     the indexer skips edge handling entirely for that file.

Language strategies whose tree-sitter grammar isn't installed are dropped from
the active registry (build_registry), so a new language is "config + pip install
tree_sitter_<lang>": the entry is always here; it activates when the grammar is.

CUSTOM strategies plug in WITHOUT forking: `index_repo(root,
strategies=[MyStrategy()])`. They are checked FIRST, so a custom strategy can
also override a built-in extension. See examples/02_custom_chunker.py.
"""

from __future__ import annotations

import importlib.util
from typing import Protocol, Sequence, runtime_checkable

from .chunkers import (
    GO_SPEC,
    PHP_SPEC,
    RUBY_SPEC,
    RUST_SPEC,
    CastChunker,
    FileResult,
    LangSpec,
    MarkdownChunker,
    TreeSitterChunker,
    TsChunker,
)
from .graph import extract_edges, php_class_index, php_edges, python_package_index, ts_edges


@runtime_checkable
class ChunkStrategy(Protocol):
    """Contract every chunking strategy satisfies (built-in or custom).

    chunk_file MUST return a FileResult whose chunks are an exact line
    partition of the file (validate_partition(result) == []) — that is the
    one hard engine invariant. Strategies with no dependency graph return
    None from both edge hooks."""

    exts: tuple[str, ...]

    def chunk_file(self, relpath: str, source: str) -> FileResult: ...

    def build_edge_ctx(self, sources: dict[str, str], repo_name: str): ...

    def extract_edges(self, relpath: str, source: str, ctx): ...


class PythonStrategy:
    exts = (".py",)

    def __init__(self, repo: str = ""):
        self._chunker = CastChunker(repo=repo)

    def chunk_file(self, relpath: str, source: str) -> FileResult:
        return self._chunker.chunk_file(relpath, source)

    def build_edge_ctx(self, sources: dict[str, str], repo_name: str):
        pkg_prefixes = {repo_name.replace("-", "_")}
        for rel in sources:
            parts = rel.split("/")
            if len(parts) >= 2 and parts[0] == "src":
                pkg_prefixes.add(parts[1])
        mod2file, unique_defs, qualdefs, trees = python_package_index(sources, pkg_prefixes)
        return {"mod2file": mod2file, "unique_defs": unique_defs,
                "qualdefs": qualdefs, "trees": trees, "pkg_prefixes": pkg_prefixes}

    def extract_edges(self, relpath, source, ctx):
        tree = ctx["trees"].get(relpath)
        if tree is None:           # unparsed file: no edges (delete_file already cleared)
            return None
        return extract_edges(relpath, tree, ctx["mod2file"], ctx["unique_defs"],
                             ctx["qualdefs"], ctx["pkg_prefixes"])


class TsJsStrategy:
    # TS grammar is a JS superset; .jsx routes to the tsx grammar in chunker_ts.
    exts = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

    def __init__(self, repo: str = ""):
        self._chunker = TsChunker(repo=repo)

    def chunk_file(self, relpath: str, source: str) -> FileResult:
        return self._chunker.chunk_file(relpath, source)

    def build_edge_ctx(self, sources: dict[str, str], repo_name: str):
        return set(sources.keys())   # all repo files, for relative-import resolution

    def extract_edges(self, relpath, source, ctx):
        return ts_edges(relpath, source, ctx)


class TreeSitterStrategy:
    """Generic strategy for a tree-sitter LangSpec with no graph yet (Ruby, Go,
    …). A language starts without an import resolver and retrieval still works;
    an edge extractor can be added later without touching the indexer."""

    def __init__(self, spec: LangSpec, exts: tuple[str, ...], repo: str = ""):
        self.spec = spec
        self.exts = exts
        self._chunker = TreeSitterChunker(spec, repo=repo)

    def chunk_file(self, relpath: str, source: str) -> FileResult:
        return self._chunker.chunk_file(relpath, source)

    def build_edge_ctx(self, sources: dict[str, str], repo_name: str):
        return None

    def extract_edges(self, relpath, source, ctx):
        return None


class MarkdownStrategy:
    exts = (".md", ".markdown", ".mdx")

    def __init__(self, repo: str = ""):
        self._chunker = MarkdownChunker(repo=repo)

    def chunk_file(self, relpath: str, source: str) -> FileResult:
        return self._chunker.chunk_file(relpath, source)

    def build_edge_ctx(self, sources: dict[str, str], repo_name: str):
        return None

    def extract_edges(self, relpath, source, ctx):
        return None   # docs have no graph (markdown-link edges are a future option)


class PhpStrategy(TreeSitterStrategy):
    """PHP = shape-routed chunking + a `use`-statement import graph. Modern
    (namespaced/PSR) files keep the generic tree-sitter chunker; legacy-2000s
    procedural/mixed-HTML files take the section chunker (chunker_php). The
    graph: a namespace+declaration scan maps FQCN -> file (PSR-4-agnostic),
    then each `use A\\B\\C;` / group-use / trait-use resolves to its repo file."""

    def __init__(self, repo: str = ""):
        super().__init__(PHP_SPEC, (".php",), repo=repo)
        from .chunkers.php import PhpChunker
        self._chunker = PhpChunker(repo=repo)   # replaces the generic chunker

    def build_edge_ctx(self, sources: dict[str, str], repo_name: str):
        return php_class_index(sources)

    def extract_edges(self, relpath, source, ctx):
        return php_edges(relpath, source, ctx)


# Language strategies gated on their grammar being importable. (spec, exts, module)
_TREE_SITTER_LANGS = [
    (RUBY_SPEC, (".rb",), "tree_sitter_ruby"),
    (GO_SPEC, (".go",), "tree_sitter_go"),
    (RUST_SPEC, (".rs",), "tree_sitter_rust"),
]


def _grammar_available(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def build_registry(repo: str = "", extra: Sequence[ChunkStrategy] = ()) -> list:
    """Active strategies for this index. Always-on: Python, TS/JS, Markdown.
    Optional languages are included only if their grammar is installed.
    `extra` (caller-supplied custom strategies) go FIRST: strategy_for picks
    the first extension match, so a custom strategy can claim a new content
    type or override a built-in one."""
    reg: list = [*extra, PythonStrategy(repo), TsJsStrategy(repo), MarkdownStrategy(repo)]
    for spec, exts, module in _TREE_SITTER_LANGS:
        if _grammar_available(module):
            reg.append(TreeSitterStrategy(spec, exts, repo))
    if _grammar_available("tree_sitter_php"):
        reg.append(PhpStrategy(repo))    # chunker + `use`-import graph
    return reg


def strategy_for(registry: list, relpath: str):
    """First strategy whose ext matches, or None (file is skipped)."""
    dot = relpath.rfind(".")
    if dot == -1:
        return None
    ext = relpath[dot:]
    for s in registry:
        if ext in s.exts:
            return s
    return None


def all_exts(registry: list) -> tuple[str, ...]:
    """Every extension the active registry can index — drives discover()."""
    return tuple(e for s in registry for e in s.exts)
