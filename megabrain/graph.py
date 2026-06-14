"""Per-file import/call edge extraction. Python validated in phase 4;
TypeScript edges from import statements with relative-path resolution."""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import PurePosixPath

_TS_IMPORT = re.compile(
    r"""(?:import|export)\s+[^'"]*?from\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)""")


def ts_edges(rel: str, source: str, all_files: set[str]) -> list[tuple[str, str]]:
    """Resolve relative TS/JS imports to repo files. Returns [(dst, 'import')]."""
    base = PurePosixPath(rel).parent
    out = set()
    for m in _TS_IMPORT.finditer(source):
        spec = m.group(1) or m.group(2)
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
        for cand in (f"{stem}.ts", f"{stem}.tsx", f"{stem}.js",
                     f"{stem}/index.ts", f"{stem}/index.tsx", stem):
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


def extract_edges(rel: str, tree: ast.Module, mod2file: dict[str, str],
                  unique_defs: dict[str, str], qualdefs: dict[str, str],
                  pkg_prefixes: set[str]) -> list[tuple[str, str]]:
    """Return [(dst_file, kind)] for one file. kind: import | call."""
    edges: set[tuple[str, str]] = set()
    imported: dict[str, str] = {}
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
        elif isinstance(node, ast.Import):
            for a in node.names:
                if any(a.name.split(".")[0] == p for p in pkg_prefixes):
                    sf = mod2file.get(a.name)
                    if sf:
                        edges.add((sf, "import"))
                        imported[a.asname or a.name] = sf
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else None)
            if not callee:
                continue
            tgt = imported.get(callee) or unique_defs.get(callee)
            if tgt and tgt != rel:
                edges.add((tgt, "call"))
    return [(dst, kind) for dst, kind in edges if dst != rel]
