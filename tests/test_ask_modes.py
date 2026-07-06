"""ask content modes: code-only (default) · --docs (docs only) · --with-docs
(code + docs). Pure candidate-selection tests, no network."""

from megabrain.ask import _candidates


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


def test_include_docs_keeps_both():
    assert _files(include_docs=True) == [
        "src/a.py", "docs/guide.md", "src/b.py", "README.md"]


def test_docs_only_wins_over_include():
    # nonsensical combo: docs_only is the stronger, explicit filter
    assert _files(docs_only=True, include_docs=True) == ["docs/guide.md", "README.md"]
