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
also override a built-in extension. See the megabrain-examples repo.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

from ..chunkers import (
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
from .graph import (
    extract_edges,
    go_edges,
    go_package_index,
    php_class_index,
    php_edges,
    python_package_index,
    ruby_edges,
    ts_edges,
)

# Bump whenever an edge extractor changes or a language GAINS one. Edges are
# derived data with no embedding cost, but the indexer only re-extracts them
# for sha-changed files — so without this, a repo indexed by an older engine
# keeps its old (or empty) graph forever, and only a full `--force` (which
# re-embeds everything, for real money) would fix it. On a version change the
# indexer re-extracts edges for every file, embeddings untouched.
#   1: python + ts/js + php
#   2: ruby (require/autoload) + go (imports + same-package siblings)
#   3: ts/js resolves TypeScript-ESM `./x.js` specifiers to x.ts
EDGE_SCHEMA = 3


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
    """Generic strategy for a tree-sitter LangSpec with no graph yet (Rust,
    …). A language starts without an import resolver and retrieval still works;
    an edge extractor is added later by SUBCLASSING (Php/Ruby/GoStrategy),
    never by touching the indexer."""

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
        from ..chunkers.php import PhpChunker
        self._chunker = PhpChunker(repo=repo)   # replaces the generic chunker

    def build_edge_ctx(self, sources: dict[str, str], repo_name: str):
        return php_class_index(sources)

    def extract_edges(self, relpath, source, ctx):
        return php_edges(relpath, source, ctx)


class RubyStrategy(TreeSitterStrategy):
    """Ruby = generic tree-sitter chunking + a require graph: `require_relative`
    resolves against the file, `require`/`autoload` through load-path
    candidates (lib/, sub-gem lib/, the file's own dir). See ruby_edges."""

    def __init__(self, repo: str = ""):
        super().__init__(RUBY_SPEC, (".rb",), repo=repo)

    def build_edge_ctx(self, sources: dict[str, str], repo_name: str):
        return set(sources)              # all repo files, for require resolution

    def extract_edges(self, relpath, source, ctx):
        return ruby_edges(relpath, source, ctx)


class GoStrategy(TreeSitterStrategy):
    """Go = generic tree-sitter chunking + a two-lane graph: in-repo imports
    pinned to the defining file via `alias.Name` uses, plus same-package edges
    (sibling files of one package need no import to call each other — that IS
    most of a Go repo's structure). See go_edges/go_package_index."""

    def __init__(self, repo: str = ""):
        super().__init__(GO_SPEC, (".go",), repo=repo)

    def build_edge_ctx(self, sources: dict[str, str], repo_name: str):
        return go_package_index(sources)

    def extract_edges(self, relpath, source, ctx):
        return go_edges(relpath, source, ctx)


# Language strategies gated on their grammar being importable. (spec, exts, module)
_TREE_SITTER_LANGS = [
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
    if _grammar_available("tree_sitter_ruby"):
        reg.append(RubyStrategy(repo))   # chunker + require graph
    if _grammar_available("tree_sitter_go"):
        reg.append(GoStrategy(repo))     # chunker + import/package graph
    if _grammar_available("tree_sitter_php"):
        reg.append(PhpStrategy(repo))    # chunker + `use`-import graph
    return reg


def builtin_strategy_for(ext: str, repo: str = ""):
    """The BUILT-IN strategy instance that handles `ext` (no custom/repo-local
    strategies). A specialization strategy delegates its common case to this —
    the shape-router pattern: handle the special shape, hand everything else to
    the engine's own chunker so normal files are chunked identically."""
    for s in build_registry(repo):
        if ext in s.exts:
            return s
    return None


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


# ------------------------------------------------- repo-local strategies (forge)
# A repo can carry its own vetted strategies in `.megabrain/strategies/*.py`
# (written by `megabrain forge`, or by hand + `megabrain trust`). index_repo
# loads them automatically — including on the 60s auto-refresh — so custom
# extensions never fall out of the index. Loading executes repo-provided code,
# so it is TRUST-GATED: a module only loads when its sha256 matches the entry
# in the USER-level trust store (~/.megabrain/trust.json), which a repo cannot
# write. forge records the sha on install; `megabrain trust <repo>` records it
# for hand-written modules; any later edit un-trusts the file until re-approved.

STRATEGY_DIR = ".megabrain/strategies"
TRUST_STORE = Path.home() / ".megabrain" / "trust.json"

log = logging.getLogger(__name__)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_trust() -> dict:
    try:
        return json.loads(TRUST_STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def trust_file(path: Path) -> None:
    """Record `path`'s current sha in the user trust store (approve it)."""
    trust = _read_trust()
    trust[Path(path).resolve().as_posix()] = _sha256(Path(path))
    TRUST_STORE.parent.mkdir(parents=True, exist_ok=True)
    TRUST_STORE.write_text(json.dumps(trust, indent=1, sort_keys=True), encoding="utf-8")


def is_trusted(path: Path) -> bool:
    p = Path(path).resolve()
    return _read_trust().get(p.as_posix()) == _sha256(p)


def instantiate_strategies(code: str, repo: str, origin: str) -> list:
    """Exec a strategy module and instantiate every ChunkStrategy defined in it
    (classes with an `exts` tuple + the three hooks). Raises on any error —
    callers decide whether that is fatal (forge) or a skip (loader)."""
    ns: dict = {"__name__": f"megabrain_repo_strategies:{origin}"}
    exec(compile(code, origin, "exec"), ns)      # noqa: S102 — trust-gated by caller
    out = []
    for obj in ns.values():
        if not (isinstance(obj, type) and isinstance(getattr(obj, "exts", None), tuple)):
            continue
        if not all(callable(getattr(obj, m, None))
                   for m in ("chunk_file", "build_edge_ctx", "extract_edges")):
            continue
        try:
            out.append(obj(repo=repo))
        except TypeError:
            out.append(obj())
    if not out:
        raise ValueError(f"{origin}: no ChunkStrategy class found")
    return out


def load_repo_strategies(root: Path, repo: str = "") -> list:
    """Trusted strategies from `<root>/.megabrain/strategies/*.py`. Untrusted
    or broken modules are skipped WITH a warning (never silently): a skipped
    module means its extensions stop being discovered, and warning is what
    keeps that visible."""
    sdir = Path(root) / STRATEGY_DIR
    if not sdir.is_dir():
        return []
    out = []
    for f in sorted(sdir.glob("*.py")):
        if not is_trusted(f):
            log.warning("%s is not trusted — skipped (its extensions will drop "
                        "from the index). Review it, then run: megabrain trust %s",
                        f, Path(root))
            continue
        try:
            out.extend(instantiate_strategies(f.read_text(encoding="utf-8"), repo,
                                              f.as_posix()))
        except Exception:                                   # noqa: BLE001
            log.warning("repo strategy %s failed to load — skipped", f, exc_info=True)
    return out
