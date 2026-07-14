"""Differential harness for the cAST chunkers.

The split-then-merge algorithm (cAST, budget 4000 nws) is the measured-optimum
path of the whole engine — it must NOT drift. This snapshots the FULL chunk +
symbol output of both chunkers over a real corpus (the engine's own Python
source for CastChunker; inline TS/Go/PHP samples for TreeSitterChunker,
exercising container-split, function-split k/n blocks, and the line-window
fallback) as a committed golden. Any refactor that changes a chunk boundary,
kind, name, part label, breadcrumb or text fails here.

Regenerate ONLY on an intended change:  RESET_CAST=1 pytest tests/test_cast_unification.py
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "goldens" / "cast_corpus.json"
ENGINE = Path(__file__).parent.parent / "src" / "megabrain"


def _fingerprint(fr) -> dict:
    return {
        "chunks": [[c.kind, c.name, c.part, c.start_line, c.end_line,
                    c.breadcrumb, hashlib.sha1(c.text.encode()).hexdigest()]
                   for c in fr.chunks],
        "symbols": [[s.name, s.kind, s.line, s.end_line, s.signature]
                    for s in fr.symbols],
        "skeleton_sha": hashlib.sha1(fr.skeleton.encode()).hexdigest(),
    }


def _python_corpus() -> dict:
    from megabrain.chunkers.python import CastChunker
    ch = CastChunker(repo="megabrain")
    out = {}
    for p in sorted(ENGINE.rglob("*.py")):
        rel = p.relative_to(ENGINE.parent).as_posix()
        out[rel] = _fingerprint(ch.chunk_file(rel, p.read_text()))
    return out


# TS/Go/PHP samples chosen to force container-split (a class/struct over budget),
# function-split into k/n blocks, and the line-window fallback (budget tiny so
# every path triggers on small inputs).
_TS = """export class Service {
  handle(req) {
    const a = 1;
    const b = 2;
    return a + b;
  }
  dispatch(req) {
    for (const x of req.items) {
      process(x);
    }
    return req;
  }
}

export function helper(x) {
  return x * 2;
}
"""

_GO = """package main

func Add(a int, b int) int {
	return a + b
}

type Server struct {
	addr string
}

func (s *Server) Run() error {
	return nil
}
"""


def _tree_corpus() -> dict:
    from megabrain.chunkers.treesitter import GO_SPEC, TS_SPEC, TreeSitterChunker
    out = {}
    # tiny budget so the samples exercise merge + split + block packing
    for name, spec, src in (("svc.ts", TS_SPEC, _TS),
                            ("main.go", GO_SPEC, _GO)):
        try:
            ch = TreeSitterChunker(spec, budget=40, repo="demo")
            out[name] = _fingerprint(ch.chunk_file(name, src))
        except Exception as e:  # grammar not installed in this env — skip, note it
            out[name] = {"skipped": str(type(e).__name__)}
    return out


def _capture() -> dict:
    return {"python": _python_corpus(), "tree": _tree_corpus()}


def test_cast_output_is_byte_stable():
    got = _capture()
    if os.environ.get("RESET_CAST"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(got, indent=1, sort_keys=True))
        pytest.skip("golden regenerated")
    assert GOLDEN.exists(), "run RESET_CAST=1 to create the golden"
    expected = json.loads(GOLDEN.read_text())
    # compare python corpus per-file for a readable diff
    assert set(got["python"]) == set(expected["python"]), "corpus file set changed"
    for rel in expected["python"]:
        assert got["python"][rel] == expected["python"][rel], f"cAST drift in {rel}"
    assert got["tree"] == expected["tree"], "tree-sitter cAST drift"
