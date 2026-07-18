"""TS chunker tests — same guarantees as the Python chunker.
Run: python3 -m pytest tests/test_chunker_ts.py -q"""

from megabrain.chunkers import TsChunker, validate_partition
from megabrain.chunkers import nws as _nws


def chunk(src, budget=4000, name="mod.ts"):
    return TsChunker(budget=budget, repo="test").chunk_file(name, src)


def test_small_file_single_chunk():
    src = "import { x } from './y'\n\nexport function f(a: number) {\n  return a\n}\n"
    r = chunk(src)
    assert validate_partition(r) == []
    assert len(r.chunks) == 1


def test_partition_and_budget_on_split():
    body = "  const s = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'\n" * 20
    src = "\n".join(f"export function f{i}() {{\n{body}}}" for i in range(6))
    r = chunk(src, budget=1500)
    assert validate_partition(r) == []
    assert len(r.chunks) > 1


def test_class_split_methods_with_breadcrumbs():
    methods = "\n".join(
        f"  m{i}(x: number) {{\n" + f"    const v = 'zzzzzzzzzzzzzzzzzzzzzzzz{i}'\n" * 12 + "  }\n"
        for i in range(8))
    src = f"export class Big {{\n{methods}}}\n"
    r = chunk(src, budget=900)
    assert validate_partition(r) == []
    named = [c for c in r.chunks if c.name and "Big.m" in c.name]
    assert named
    assert all("class Big" in c.breadcrumb for c in named)


def test_interface_and_type_symbols():
    src = ("export interface Cfg { a: string }\n"
           "export type Status = 'idle' | 'busy'\n"
           "export const LIMIT = 5\n"
           "export class Svc {\n  run(q: string) { return q }\n}\n")
    r = chunk(src)
    names = {s.name: s.kind for s in r.symbols}
    assert names["Cfg"] == "interface"
    assert names["Status"] == "type"
    assert names["LIMIT"] == "const"
    assert names["Svc"] == "class"
    assert names["Svc.run"] == "method"


def test_skeleton():
    src = "export class A {\n  go() {}\n}\nexport function top(): void {}\n"
    r = chunk(src)
    assert "class A" in r.skeleton and "top()" in r.skeleton


def test_oversized_inner_interface_respects_budget():
    """Generated .d.ts shape: giant interface inside a namespace must line-split."""
    members = "".join(f"  field{i}: {{ a: string; b: number; c: 'xxxxxxxxxxxxxxxxxxxx' }}\n" for i in range(300))
    src = f"export namespace Dap {{\n  export interface Api {{\n{members}  }}\n}}\n"
    r = chunk(src, budget=2000)
    assert validate_partition(r) == []
    for c in r.chunks:
        if c.end_line > c.start_line:
            assert _nws(c.text) <= 2000, f"chunk L{c.start_line}-{c.end_line} over budget"


# ---------------------------------------------------------------- edges
# ts_edges shipped with NO tests, which is how the TypeScript-ESM miss below
# survived: the resolver looked correct and silently produced nothing.

from megabrain.indexing.graph import ts_edges  # noqa: E402

TS_FILES = {
    "source/index.ts",
    "source/core/Ky.ts",
    "source/core/constants.ts",
    "source/errors/HTTPError.ts",
    "source/utils/merge.ts",
    "source/types/options.d.ts",
    "source/legacy/shim.js",
    "source/widgets/index.tsx",
    "test/main.ts",
}


def test_ts_esm_js_specifier_resolves_to_the_ts_file():
    """TypeScript ESM (moduleResolution node16/nodenext) REQUIRES writing the
    compiled `.js` specifier while the file on disk is `.ts` — the norm in
    modern TS. Missing this drew an empty graph for the whole repo (ky)."""
    src = (
        "import Ky from './core/Ky.js'\n"
        "import {HTTPError} from './errors/HTTPError.js'\n"
        "import type {Options} from './types/options.js'\n"   # -> .d.ts
        "export {merge} from './utils/merge.js'\n"
    )
    assert dict(ts_edges("source/index.ts", src, TS_FILES)) == {
        "source/core/Ky.ts": "import",
        "source/errors/HTTPError.ts": "import",
        "source/types/options.d.ts": "import",
        "source/utils/merge.ts": "import",
    }


def test_ts_extensionless_directory_and_real_js_still_resolve():
    """The rewrite must not cost the older shapes: extensionless specifiers,
    a directory's index, and a `.js` import that really IS a .js file."""
    src = (
        "import c from './core/constants'\n"       # extensionless
        "import w from './widgets'\n"              # directory -> index.tsx
        "import s from './legacy/shim.js'\n"       # a real .js on disk
        "const {x} = require('./core/Ky.js')\n"    # require, ESM-style spec
    )
    assert dict(ts_edges("source/index.ts", src, TS_FILES)) == {
        "source/core/constants.ts": "import",
        "source/widgets/index.tsx": "import",
        "source/legacy/shim.js": "import",
        "source/core/Ky.ts": "import",
    }


def test_ts_bare_and_unresolved_specifiers_never_edge():
    src = (
        "import ky from 'ky'\n"                    # bare: a package, not a file
        "import x from 'node:fs'\n"
        "import y from './nope.js'\n"              # nothing on disk
        "import z from '../outside.js'\n"
    )
    assert ts_edges("source/index.ts", src, TS_FILES) == []


def test_ts_parent_traversal_and_no_self_edge():
    src = "import Ky from '../source/core/Ky.js'\nimport me from './main.js'\n"
    assert ts_edges("test/main.ts", src, TS_FILES) == [("source/core/Ky.ts", "import")]
