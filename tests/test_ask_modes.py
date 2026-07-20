"""ask content modes: code-only (default) · --docs (docs only). Pure
candidate-selection tests, no network.

There is no third "both" mode on purpose. `--with-docs` / `include_docs`
existed until 0.17.1 and did not do what it named: with neither filter on,
retrieval ranks code and prose together and the prose wins — on sinatra,
`--with-docs "how are routes defined and dispatched"` came back with
CORE = [README.md] and no code at all. Answering with both sides needs two
lanes merged, not one blended ranking, so the flag was removed rather than
left lying. test_no_blend_mode_survives is the guard against it creeping back.
"""

import inspect

from megabrain.ask.narrator import _candidates


def _chunk(name="f"):
    return {"name": name, "kind": "function", "start_line": 1, "end_line": 2,
            "text": "x = 1\n"}


RES = {
    "tier1": [
        {"file": "src/a.py", "chunks": [_chunk("a")]},
        {"file": "docs/guide.md", "chunks": [_chunk("# Guide")]},
    ],
    "tier2": [
        {"file": "src/b.py", "best_chunk": _chunk("b")},
        {"file": "README.md", "best_chunk": _chunk("# Readme")},
    ],
}


def _files(**kw):
    return [c["file"] for c in _candidates(RES, **kw)]


def test_default_is_code_only():
    assert _files() == ["src/a.py", "src/b.py"]


def test_docs_only():
    assert _files(docs_only=True) == ["docs/guide.md", "README.md"]


def test_the_two_modes_partition_the_bundle():
    """Every candidate lands in exactly one mode — no file is dropped by both
    and none is served by both. This is what makes "code OR docs" a real
    partition rather than two overlapping filters."""
    every = [t["file"] for t in RES["tier1"]] + [t["file"] for t in RES["tier2"]]
    assert sorted(_files() + _files(docs_only=True)) == sorted(every)


def test_no_blend_mode_survives():
    """The removed flag must not come back through any surface."""
    assert "include_docs" not in inspect.signature(_candidates).parameters
