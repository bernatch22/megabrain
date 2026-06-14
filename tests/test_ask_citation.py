"""ask citation parsing — the engine must splice EVERY citation the model emits.

Regression: the prompt's chunk headers read "L1-172", so the model often mirrors
that and writes [[0:L1-172]] instead of [[0:1-172]]. The citation regex must accept
the L prefix (and stray spaces) or the citation leaks as raw text and no code is
spliced. Run: python3 -m pytest tests/test_ask_citation.py -q
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from megabrain.ask import _SEL, _candidates, cited_files, render_ask


def test_sel_accepts_plain_l_prefix_and_spaces():
    assert _SEL.findall("[[3]]") == [("3", "", "")]
    assert _SEL.findall("[[3:705-731]]") == [("3", "705", "731")]
    assert _SEL.findall("[[0:L1-172]]") == [("0", "1", "172")]      # the bug
    assert _SEL.findall("[[0:l5-9]]") == [("0", "5", "9")]
    assert _SEL.findall("[[12: L1 - 80 ]]") == [("12", "1", "80")]
    # single brackets are never citations (model may mention [3] in prose)
    assert _SEL.findall("see [3] above") == []


def _out(text):
    cand = {"file": "docs/x.md", "name": "Intro", "kind": "section",
            "start_line": 1, "end_line": 5,
            "text": "line1\nline2\nline3\nline4\nline5\n"}
    return {"cands": [cand], "text": text, "file_syms": {"docs/x.md": []},
            "query": "q", "repo": "r", "retrieval_ms": 1, "llm_ms": 1,
            "result": {"tier1": [], "tier2": []}}


def test_render_splices_l_prefixed_citation():
    r = render_ask(_out("See the protocol.\n[[0:L1-3]]\nDone."))
    assert "[[" not in r                     # nothing leaked as raw text
    assert "```" in r                         # real code spliced
    assert "line1" in r and "line3" in r      # range L1-3 emitted
    assert "line4" not in r                   # and bounded


def test_render_splices_whole_chunk_citation():
    r = render_ask(_out("Whole thing:\n[[0]]\n"))
    assert "[[" not in r and "line5" in r      # full chunk spliced


def test_cited_files_counts_l_prefixed():
    assert cited_files(_out("[[0:L1-3]]")) == ["docs/x.md"]


def test_ask_candidates_code_only_default_and_docs_only():
    """ask is code-only by default; docs_only=True flips to docs-only."""
    ch = {"name": "f", "kind": "function", "start_line": 1, "end_line": 3, "text": "x\n"}
    res = {"tier1": [{"file": "src/a.ts", "chunks": [ch]},
                     {"file": "docs/g.md", "chunks": [ch]}],
           "tier2": [{"file": "docs/h.md", "best_chunk": ch},
                     {"file": "src/b.ts", "best_chunk": ch}]}
    assert [c["file"] for c in _candidates(res)] == ["src/a.ts", "src/b.ts"]
    assert [c["file"] for c in _candidates(res, docs_only=True)] == ["docs/g.md", "docs/h.md"]
