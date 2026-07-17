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


def ts_edges(rel: str, source: str, all_files: set[str]) -> list[tuple[str, str]]:
    """Resolve relative TS/JS imports to repo files. Returns [(dst, 'import')]."""
    base = PurePosixPath(rel).parent
    out = set()
    for m in _TS_IMPORT.finditer(source):
        spec = next((g for g in m.groups() if g), None)
        if not spec or not spec.startswith("."):
            continue
        target = PurePosixPath(str((base / spec)))
        # normalize ../
        parts: list[str] = []
        for p in target.parts:
            if p == "..":
                if parts:
                    parts.pop()
            elif p != ".":
                parts.append(p)
        stem = "/".join(parts)
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

    Call edges are RECEIVER-AWARE: `alias.f()` where the alias is an in-repo
    import resolves to that alias's file; where the alias is an EXTERNAL
    import (stdlib/site-packages) it produces no edge — `re.search(...)` once
    fabricated an edge to the repo's unique `search()` def, and every stdlib
    method colliding with a repo name did the same. Unknown receivers (local
    variables, self) keep the unique-def fallback: without type inference
    that evidence is weak but usually right, and dropping it would cost real
    edges (validated recall)."""
    edges: set[tuple[str, str]] = set()
    imported: dict[str, str] = {}
    ext_aliases: set[str] = set()        # imported names that are NOT this repo

    def _in_repo(module: str) -> bool:
        """Does the dotted module resolve to ANYTHING indexed? pkg_prefixes
        alone misclassifies top-level repo modules (a repo with no packages
        imports `web` absolutely) — external means external for real."""
        return (module in mod2file
                or any(k.startswith(module + ".") for k in mod2file))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if any(node.module.split(".")[0] == p for p in pkg_prefixes):
                sf = mod2file.get(node.module)
                if sf:
                    edges.add((sf, "import"))
                    for a in node.names:
                        imported[a.asname or a.name] = qualdefs.get(
                            f"{node.module}.{a.name}", sf)
                else:
                    for a in node.names:
                        sub = mod2file.get(f"{node.module}.{a.name}")
                        if sub:
                            edges.add((sub, "import"))
                            imported[a.asname or a.name] = sub
            elif node.level == 0 and not _in_repo(node.module):
                for a in node.names:     # stdlib/3rd-party: never edge material
                    ext_aliases.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if any(a.name.split(".")[0] == p for p in pkg_prefixes):
                    sf = mod2file.get(a.name)
                    if sf:
                        edges.add((sf, "import"))
                        imported[a.asname or a.name] = sf
                elif not _in_repo(a.name):
                    ext_aliases.add(a.asname or a.name.split(".")[0])
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                callee, recv = node.func.id, None
            elif isinstance(node.func, ast.Attribute):
                callee, recv = node.func.attr, _recv_base(node.func)
            else:
                continue
            if recv is not None:
                if recv in imported:     # exact: the alias's own file
                    tgt = imported[recv]
                    if tgt != rel:
                        edges.add((tgt, "call"))
                    continue
                if recv in ext_aliases:  # stdlib/external: never an edge
                    continue
            tgt = imported.get(callee) or unique_defs.get(callee)
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
