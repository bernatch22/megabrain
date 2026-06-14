"""TS chunker tests — same guarantees as the Python chunker.
Run: python3 -m pytest tests/test_chunker_ts.py -q"""

from megabrain.chunker import validate_partition, nws as _nws
from megabrain.chunker_ts import TsChunker


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
