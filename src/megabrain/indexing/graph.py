"""Per-file import/call edge extraction. Python validated in phase 4;
TypeScript edges from import statements with relative-path resolution."""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import PurePosixPath

_TS_IMPORT = re.compile(
    r"""(?:import|export)\s+[^'"]*?from\s+['"]([^'"]+)['"]"""   # import x from / export * from
    r"""|require\(\s*['"]([^'"]+)['"]\s*\)"""                    # require('x')
    r"""|import\s*\(\s*['"]([^'"]+)['"]\s*\)"""                  # dynamic import('x')
    r"""|import\s+['"]([^'"]+)['"]""")                           # side-effect import 'x'


def _norm_rel(path: PurePosixPath) -> str:
    """Collapse `.`/`..` segments of a repo-relative path (posix, no fs)."""
    parts: list[str] = []
    for p in path.parts:
        if p == "..":
            if parts:
                parts.pop()
        elif p != ".":
            parts.append(p)
    return "/".join(parts)


def ts_edges(rel: str, source: str, all_files: set[str]) -> list[tuple[str, str]]:
    """Resolve relative TS/JS imports to repo files. Returns [(dst, 'import')]."""
    base = PurePosixPath(rel).parent
    out = set()
    for m in _TS_IMPORT.finditer(source):
        spec = next((g for g in m.groups() if g), None)
        if not spec or not spec.startswith("."):
            continue
        stem = _norm_rel(base / spec)
        for cand in (f"{stem}.ts", f"{stem}.tsx", f"{stem}.js", f"{stem}.jsx",
                     f"{stem}.mjs", f"{stem}.cjs",
                     f"{stem}/index.ts", f"{stem}/index.tsx", f"{stem}/index.js",
                     stem):
            if cand in all_files and cand != rel:
                out.add((cand, "import"))
                break
    return sorted(out)


def python_package_index(file_sources: dict[str, str], pkg_prefixes: set[str]):
    """Build module->file map and symbol def maps across the repo.
    file_sources: relpath -> source. Returns (mod2file, unique_defs, qualdefs)."""
    mod2file: dict[str, str] = {}
    defs: dict[str, set[str]] = defaultdict(set)
    qualdefs: dict[str, str] = {}
    trees: dict[str, ast.Module] = {}
    for rel, src in file_sources.items():
        if not rel.endswith(".py"):
            continue
        mod = rel.replace(".py", "").replace("/", ".")
        for marker in ("src.",):
            if mod.startswith(marker):
                mod = mod[len(marker):]
        if mod.endswith(".__init__"):
            mod = mod[:-9]
        mod2file[mod] = rel
        try:
            t = ast.parse(src)
        except SyntaxError:
            continue
        trees[rel] = t
        for node in ast.walk(t):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs[node.name].add(rel)
                qualdefs[f"{mod}.{node.name}"] = rel
    unique = {n: next(iter(fs)) for n, fs in defs.items() if len(fs) == 1}
    return mod2file, unique, qualdefs, trees


def _recv_base(func: ast.Attribute) -> str | None:
    """The base Name of an attribute call's receiver: `x.f()` -> x,
    `Cls(...).f()` -> Cls, `a.b.c.f()` -> a. None when unresolvable."""
    v: ast.expr = func.value
    while isinstance(v, ast.Attribute):
        v = v.value
    if isinstance(v, ast.Call) and isinstance(v.func, ast.Name):
        return v.func.id
    return v.id if isinstance(v, ast.Name) else None


def extract_edges(rel: str, tree: ast.Module, mod2file: dict[str, str],
                  unique_defs: dict[str, str], qualdefs: dict[str, str],
                  pkg_prefixes: set[str]) -> list[tuple[str, str]]:
    """Return [(dst_file, kind)] for one file. kind: import | call.

    A call edge exists ONLY through a resolved import: `f()` where f was
    imported from a repo module, or `alias.f()` / `Alias(...).f()` where the
    alias is an imported repo module/symbol. A cross-file Python call that
    isn't imported can't execute — so a bare-name match is never evidence.
    The old unique-def fallback minted phantoms out of stdlib collisions
    (`re.search` -> the repo's only `search()`, `qs.get` -> Registry.get);
    graphify's extractor follows the same imports-only rule. `unique_defs`
    stays in the signature for compatibility but is no longer consulted."""
    del unique_defs, pkg_prefixes        # legacy params — mod2file IS the proof
    edges: set[tuple[str, str]] = set()
    imported: dict[str, str] = {}
    # this file's dotted module path (src. stripped like mod2file's keys,
    # __init__ KEPT so relative levels count correctly)
    own = rel[:-3].replace("/", ".")
    if own.startswith("src."):
        own = own[4:]
    own_parts = own.split(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:               # relative: from . / .. / ..mod import X
                base = own_parts[:-node.level]
                target = ".".join(base + (node.module.split(".")
                                          if node.module else []))
            else:
                target = node.module or ""
            if not target and not node.level:
                continue
            sf = mod2file.get(target)
            if sf:
                edges.add((sf, "import"))
            for a in node.names:
                # `from PKG import submodule` — the submodule wins the alias
                # (calls on it belong to ITS file, not the package __init__)
                sub = mod2file.get(f"{target}.{a.name}" if target else a.name)
                if sub:
                    edges.add((sub, "import"))
                    imported[a.asname or a.name] = sub
                elif sf:
                    imported[a.asname or a.name] = qualdefs.get(
                        f"{target}.{a.name}", sf)
        elif isinstance(node, ast.Import):
            for a in node.names:
                sf = mod2file.get(a.name)
                if sf:
                    edges.add((sf, "import"))
                    imported[a.asname or a.name] = sf
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                tgt = imported.get(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                recv = _recv_base(node.func)
                tgt = imported.get(recv) if recv else None
            else:
                continue
            if tgt and tgt != rel:
                edges.add((tgt, "call"))
    return [(dst, kind) for dst, kind in edges if dst != rel]


# ---------------------------------------------------------------- PHP

_PHP_NS = re.compile(r"^\s*namespace\s+([A-Za-z_][\w\\]*)\s*[;{]", re.M)
_PHP_DECL = re.compile(
    r"^\s*(?:abstract\s+|final\s+|readonly\s+)*(?:class|interface|trait|enum)\s+"
    r"([A-Za-z_]\w*)", re.M)
# `use A\B\C;` / `use A\B as X;` — both top-level imports and trait-use inside a
# class body (a trait IS a file dependency, so both become edges). Skips
# `use function`/`use const`.
_PHP_USE = re.compile(
    r"^\s*use\s+(?!function\b|const\b)([A-Za-z_][\w\\]*)(?:\s+as\s+\w+)?\s*;", re.M)
# group form: `use A\B\{C, D as E};`
_PHP_USE_GROUP = re.compile(
    r"^\s*use\s+(?!function\b|const\b)([A-Za-z_][\w\\]*)\\\{([^}]+)\}\s*;", re.M)


def php_class_index(sources: dict[str, str]) -> dict[str, str]:
    """FQCN -> relpath for every class/interface/trait/enum declared in the repo
    (PSR-4-agnostic: built from the actual `namespace` + declarations, so it
    works whatever the folder layout)."""
    fqcn2file: dict[str, str] = {}
    for rel, src in sources.items():
        if not rel.endswith(".php"):
            continue
        m = _PHP_NS.search(src)
        ns = m.group(1) if m else ""
        for d in _PHP_DECL.finditer(src):
            fqcn = f"{ns}\\{d.group(1)}" if ns else d.group(1)
            fqcn2file.setdefault(fqcn, rel)
    return fqcn2file


# ---------------------------------------------------------------- Ruby

_RB_REQUIRE = re.compile(
    r"""^\s*(require_relative|require)\s*\(?\s*['"]([^'"]+)['"]""", re.M)
# `autoload :Const, 'path'` loads through the SAME load-path as require —
# rack-protection wires every strategy this way, so it IS the import graph.
_RB_AUTOLOAD = re.compile(
    r"""^\s*autoload\s*\(?\s*:\w+\s*,\s*['"]([^'"]+)['"]""", re.M)


def ruby_edges(rel: str, source: str, all_files: set[str]) -> list[tuple[str, str]]:
    """Resolve Ruby requires to repo files. Returns [(dst, 'import')].

    `require_relative` is exact (relative to the file). `require`/`autoload`
    use load-path semantics without knowing $LOAD_PATH: try the repo root's
    `lib/<spec>.rb`, the bare `<spec>.rb`, the requiring file's own dir (test
    suites add it to the path for `require 'test_helper'`), then any sub-gem's
    `*/lib/<spec>.rb` (monorepos: sinatra ships rack-protection + contrib).
    Nothing matches -> stdlib/gem, no edge — absence of proof is absence."""
    base = PurePosixPath(rel).parent
    specs = [(m.group(1) == "require_relative", m.group(2))
             for m in _RB_REQUIRE.finditer(source)]
    specs += [(False, m.group(1)) for m in _RB_AUTOLOAD.finditer(source)]
    out = set()
    for is_relative, spec in specs:
        if not spec.endswith(".rb"):
            spec += ".rb"
        if is_relative:
            cand = _norm_rel(base / spec)
            if cand in all_files and cand != rel:
                out.add((cand, "import"))
            continue
        hit = next((c for c in (f"lib/{spec}", spec, _norm_rel(base / spec))
                    if c in all_files), None)
        if hit is None:
            hit = next(iter(sorted(f for f in all_files
                                   if f.endswith(f"/lib/{spec}"))), None)
        if hit and hit != rel:
            out.add((hit, "import"))
    return sorted(out)


# ---------------------------------------------------------------- Go

_GO_PKG = re.compile(r"^package\s+(\w+)", re.M)
# Top-level decls only — methods are deliberately absent: a method is reachable
# only through a receiver, so a bare-name or `pkg.Name` match can never mean a
# method, and indexing them would let `binding.Default` hit a type's `Default`.
_GO_TOP_DECL = re.compile(
    r"^(?:func\s+([A-Za-z_]\w*)\s*[([]"          # func Name( / func Name[T](
    r"|type\s+([A-Za-z_]\w*)"
    r"|var\s+([A-Za-z_]\w*)"
    r"|const\s+([A-Za-z_]\w*))", re.M)
_GO_DECL_BLOCK = re.compile(r"^(?:var|const|type)\s*\(\n(.*?)^\)", re.M | re.S)
_GO_BLOCK_NAME = re.compile(r"^\t+([A-Za-z_]\w*)|^ +([A-Za-z_]\w*)", re.M)
_GO_IMPORT_ONE = re.compile(r'^import\s+(?:(\w+|\.|_)\s+)?"([^"]+)"', re.M)
_GO_IMPORT_BLOCK = re.compile(r"^import\s*\(\n(.*?)^\)", re.M | re.S)
_GO_IMPORT_LINE = re.compile(r'^\s*(?:(\w+|\.|_)\s+)?"([^"]+)"', re.M)
# comments + string/char/backtick literals, so the usage scan never matches
# a name mentioned in prose or in a format string
_GO_STRIP = re.compile(
    r'//[^\n]*|/\*.*?\*/|"(?:[^"\\\n]|\\.)*"|`[^`]*`|\'(?:[^\'\\\n]|\\.)*\'',
    re.S)


def go_package_index(sources: dict[str, str]) -> dict:
    """Whole-repo Go prepass. Go enforces top-level name uniqueness inside a
    package, so (dir, package) -> {name: file} is an EXACT map, no types
    needed. Also records which packages live in each dir (an external
    `pkg_test` package shares the dir with `pkg`) and each package's files."""
    decls: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    pkg_of: dict[str, tuple[str, str]] = {}
    files: dict[tuple[str, str], list[str]] = defaultdict(list)
    for rel in sorted(sources):
        src = sources[rel]
        if not rel.endswith(".go"):
            continue
        m = _GO_PKG.search(src)
        if not m:
            continue
        d = str(PurePosixPath(rel).parent)
        key = ("" if d == "." else d, m.group(1))
        pkg_of[rel] = key
        files[key].append(rel)
        names = {g for dm in _GO_TOP_DECL.finditer(src) for g in dm.groups() if g}
        for bm in _GO_DECL_BLOCK.finditer(src):
            names |= {g for nm in _GO_BLOCK_NAME.finditer(bm.group(1))
                      for g in nm.groups() if g}
        for nm in names:
            decls[key].setdefault(nm, rel)
    dirs: dict[str, set[str]] = defaultdict(set)
    for d, pkg in pkg_of.values():
        dirs[d].add(pkg)
    return {"decls": dict(decls), "pkg_of": pkg_of,
            "files": dict(files), "dirs": dict(dirs)}


def _go_primary_pkg(ctx: dict, d: str) -> str | None:
    """The dir's real package (skip the external `_test` package)."""
    pkgs = sorted(p for p in ctx["dirs"].get(d, ()) if not p.endswith("_test"))
    return pkgs[0] if pkgs else None


def _go_import_dir(ctx: dict, path: str) -> str | None:
    """Import path -> repo dir. go.mod isn't indexed, so the module prefix is
    unknown: strip leading segments until the remainder IS a repo dir holding
    Go files. The module root itself has no such suffix — resolve it by
    package name instead, only for dotted (domain-style) module paths so a
    stdlib `import "log"` can never hit a repo package by accident."""
    segs = path.split("/")
    for k in range(1, len(segs)):
        cand = "/".join(segs[k:])
        if cand in ctx["dirs"]:
            return cand
    if len(segs) >= 3 and "." in segs[0]:
        hits = [d for d in ctx["dirs"] if _go_primary_pkg(ctx, d) == segs[-1]]
        if len(hits) == 1:
            return hits[0]
    return None


def go_edges(rel: str, source: str, ctx: dict) -> list[tuple[str, str]]:
    """Two lanes, mirroring how Go actually links code:

    import lane — each in-repo import resolves to its package dir; then
    `alias.Name` uses in the (comment/string-stripped) source pin the edge to
    the file DEFINING Name. An import with no attributable use still edges to
    the package's representative file, so the dependency is never dropped.

    package lane — files of one package call each other with NO import at
    all (the dominant structure of a Go repo: gin's root is 40 such files).
    A bare use of a name declared in a sibling file is a 'call' edge; the
    `(?<![.\\w])` guard rejects `other.Name`, so a dotted use can't leak in."""
    key = ctx["pkg_of"].get(rel)
    stripped = _GO_STRIP.sub(" ", source)
    out: set[tuple[str, str]] = set()

    imports = [(m.group(1), m.group(2)) for m in _GO_IMPORT_ONE.finditer(source)]
    for bm in _GO_IMPORT_BLOCK.finditer(source):
        imports += [(m.group(1), m.group(2))
                    for m in _GO_IMPORT_LINE.finditer(bm.group(1))]
    for alias, path in imports:
        d = _go_import_dir(ctx, path)
        if d is None:
            continue
        pkg = _go_primary_pkg(ctx, d)
        if pkg is None or (d, pkg) == key:
            continue
        tkey = (d, pkg)
        matched = False
        if alias not in (".", "_"):
            name = alias or pkg
            use = re.compile(rf"(?<![.\w]){re.escape(name)}\.([A-Za-z_]\w*)")
            for um in use.finditer(stripped):
                dst = ctx["decls"].get(tkey, {}).get(um.group(1))
                if dst and dst != rel:
                    out.add((dst, "import"))
                    matched = True
        if not matched:                  # dot/blank import, or no resolved use
            leaf = f"{d}/{path.rsplit('/', 1)[-1]}.go" if d else \
                f"{path.rsplit('/', 1)[-1]}.go"
            fs = ctx["files"].get(tkey, [])
            dst = leaf if leaf in fs else (fs[0] if fs else None)
            if dst and dst != rel:
                out.add((dst, "import"))

    if key:
        siblings = {n: f for n, f in ctx["decls"].get(key, {}).items()
                    if f != rel and len(n) >= 2}
        if siblings:
            pat = re.compile(r"(?<![.\w])("
                             + "|".join(map(re.escape, sorted(siblings))) + r")\b")
            for m in pat.finditer(stripped):
                out.add((siblings[m.group(1)], "call"))
    return sorted(out)


def php_edges(rel: str, source: str, fqcn2file: dict[str, str]) -> list[tuple[str, str]]:
    """Resolve `use` statements (imports, aliases, group-use, trait-use) to repo
    files. Bare names also try the file's own namespace, so `use LogsActivity;`
    inside a class resolves to the sibling trait. Returns [(dst, 'import')]."""
    m = _PHP_NS.search(source)
    ns = m.group(1) if m else ""
    names: set[str] = set()
    for u in _PHP_USE.finditer(source):
        names.add(u.group(1).lstrip("\\"))
    for g in _PHP_USE_GROUP.finditer(source):
        prefix = g.group(1).lstrip("\\")
        for item in g.group(2).split(","):
            leaf = item.strip().split(" as ")[0].strip().lstrip("\\")
            if leaf:
                names.add(f"{prefix}\\{leaf}")
    out = set()
    for name in names:
        for cand in (name, f"{ns}\\{name}" if ns else name):
            dst = fqcn2file.get(cand)
            if dst and dst != rel:
                out.add((dst, "import"))
                break
    return sorted(out)
