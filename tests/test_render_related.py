"""RELATED renders as a map by default (no chunk code bodies) — the bundle
DATA keeps best_chunk untouched (ask/serve depend on it); only render() slims.
`related_code=True` restores the inline-code view. Motivated by real agent
sessions: RELATED code bodies were ~16K of a ~22K-token bundle at ~5% gold."""

from megabrain.query import render

RES = {
    "query": "q", "repo": "r", "ms": 1,
    "tier1": [{
        "file": "a.py", "score": 1.0, "neighbors": [], "symbols": [],
        "chunks": [{"name": "f", "kind": "function", "start_line": 1,
                    "end_line": 2, "part": None, "score": 1.0,
                    "text": "def f():\n    return CORE_BODY\n"}],
    }],
    "tier2": [{
        "file": "b.py", "score": 0.5, "via_graph": False, "matched": ["g"],
        "doc": None,
        "best_chunk": {"name": "g", "kind": "function", "start_line": 10,
                       "end_line": 20, "part": None,
                       "text": "def g():\n    return RELATED_BODY\n"},
        "symbols": [{"name": "g", "kind": "function", "line": 10, "end_line": 20,
                     "signature": "def g()", "doc": None}],
    }],
}


def test_default_related_is_a_map():
    out = render(RES)
    assert "CORE_BODY" in out                    # tier1 code always renders
    assert "RELATED_BODY" not in out             # tier2 body gone by default
    assert "**g** L10-20" in out                 # ...but the match POINTER stays
    assert "- `def g()` L10-20" in out           # ...and the symbols
    assert "b.py" in out                         # file never leaves the bundle


def test_full_restores_related_code():
    out = render(RES, related_code=True)
    assert "RELATED_BODY" in out


def test_compact_never_shows_bodies():
    out = render(RES, compact=True)
    assert "CORE_BODY" not in out and "RELATED_BODY" not in out
    out = render(RES, compact=True, related_code=True)
    assert "RELATED_BODY" not in out
