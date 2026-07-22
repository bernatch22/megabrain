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
from megabrain.ask.narrator import _SEL, _candidates, _parse_ranges, cited_files, render_ask


def _cites(s):
    return [(m.group(1), _parse_ranges(m.group(2))) for m in _SEL.finditer(s)]


def test_sel_accepts_plain_l_prefix_and_spaces():
    assert _cites("[[3]]") == [("3", [])]
    assert _cites("[[3:705-731]]") == [("3", [(705, 731)])]
    assert _cites("[[0:L1-172]]") == [("0", [(1, 172)])]            # the bug
    assert _cites("[[0:l5-9]]") == [("0", [(5, 9)])]
    assert _cites("[[12: L1 - 80 ]]") == [("12", [(1, 80)])]
    # single brackets are never citations (model may mention [3] in prose)
    assert _cites("see [3] above") == []


def test_sel_accepts_multi_range_citations():
    """Field report: the model wrote [[1:24-28, ...]]-style citations and they
    leaked as raw prose — models emit comma-separated ranges unprompted."""
    assert _cites("[[1:24-28, 30-42]]") == [("1", [(24, 28), (30, 42)])]
    assert _cites("[[2:L5-9,L12-20, 33-40]]") == \
        [("2", [(5, 9), (12, 20), (33, 40)])]


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


def test_multi_range_citation_splices_every_range():
    r = render_ask(_out("Two spans matter.\n[[0:1-2, 4-5]]\nDone."))
    assert "[[" not in r                      # nothing leaked
    assert "line1" in r and "line2" in r      # first range
    assert "line4" in r and "line5" in r      # second range
    assert "line3" not in r                   # the gap stays out


def _big_out(text):
    """A 100-line chunk holding three symbols; the claim names one."""
    body = "\n".join(f"code {i}" for i in range(1, 101))
    cand = {"file": "src/big.py", "name": "Big", "kind": "class",
            "start_line": 1, "end_line": 100, "text": body + "\n"}
    syms = [
        {"name": "write_heading", "kind": "method", "line": 5, "end_line": 30,
         "signature": "def write_heading(self)", "doc": None},
        {"name": "write_usage_wrapping", "kind": "method", "line": 40,
         "end_line": 60, "signature": "def write_usage_wrapping(self)",
         "doc": None},
        {"name": "write_dl", "kind": "method", "line": 70, "end_line": 95,
         "signature": "def write_dl(self)", "doc": None},
    ]
    return {"cands": [cand], "text": text, "file_syms": {"src/big.py": syms},
            "query": "q", "repo": "r", "retrieval_ms": 1, "llm_ms": 1,
            "result": {"tier1": [], "tier2": []}}


def test_whole_cite_of_huge_chunk_narrows_to_the_claim_symbol():
    """Field report: '[[k]] dumped nearly the entire class to make a point
    about two constructor kwargs'. A whole-chunk cite over the cap narrows to
    the symbol the citing sentence names, and says what it left out."""
    r = render_ask(_big_out(
        "The wrapping bug lives in write_usage_wrapping, which configures "
        "the wrapper.\n[[0]]\nDone."))
    assert "L40-60" in r                      # the claim's symbol only
    assert "code 40" in r and "code 60" in r
    assert "\ncode 5\n" not in r.split("```")[1]   # write_heading's body out
    assert "rest of chunk L1-100 not shown" in r
    assert "write_heading L5-30" in r         # the omission is itemized


def test_whole_cite_without_claim_match_stays_whole():
    """No identifier overlap between prose and any symbol -> whole chunk,
    the pre-existing behavior (fail open, never guess)."""
    r = render_ask(_big_out("Some unrelated sentence here.\n[[0]]\nDone."))
    assert "code 1" in r and "code 100" in r
    assert "rest of chunk" not in r
