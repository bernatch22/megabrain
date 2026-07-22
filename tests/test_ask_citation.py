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


# ------------------------------------------------------------- pagination

def _big_ask(n_blocks=6, block_lines=300):
    """An out whose spliced body far exceeds one page."""
    text = "Intro prose.\n" + "\n".join(
        f"Step {k}.\n[[{k}]]" for k in range(n_blocks)) + "\nDone."
    cands = []
    for k in range(n_blocks):
        body = "\n".join(f"code_{k}_{i}" for i in range(block_lines))
        cands.append({"file": f"src/m{k}.py", "name": f"fn{k}", "kind": "function",
                      "start_line": 1, "end_line": block_lines, "text": body + "\n"})
    return {"cands": cands, "text": text, "file_syms": {},
            "query": "q", "repo": "r", "retrieval_ms": 1, "llm_ms": 1,
            "result": {"tier1": [], "tier2": []}}


def test_long_ask_paginates_never_truncates(monkeypatch):
    """Field case: an 80KB ask overflowed the MCP host and the agent read a
    2KB preview. Long walkthroughs paginate at block boundaries — everything
    is delivered, nothing degraded to pointers."""
    monkeypatch.setenv("MEGABRAIN_RENDER_BUDGET", "8000")
    out = _big_ask()
    p1 = render_ask(out, page=1)
    assert "page 1/" in p1 and "page=2" in p1
    assert "Do not act on partial evidence" in p1
    # a code block is atomic: fences are balanced on every page
    total_pages = int(p1.split("page 1/")[1].split()[0])
    seen = ""
    for i in range(1, total_pages + 1):
        pg = render_ask(_big_ask(), i)
        assert pg.count("```") % 2 == 0
        seen += pg
    for k in range(6):                       # every block delivered somewhere
        assert f"code_{k}_0" in seen
    assert "not cited" not in p1             # footers close the LAST page only


def test_short_ask_is_single_page(monkeypatch):
    monkeypatch.setenv("MEGABRAIN_RENDER_BUDGET", "24000")
    r = render_ask(_out("See.\n[[0]]\nDone."))
    assert "page" not in r.split("\n")[1]    # no page tag in the header
    assert "Do not act on partial" not in r


def test_cached_ask_paginates_too(monkeypatch):
    """Page 2 is what makes pagination cheap: same question hits the flow
    cache (0ms) and slices the next page — the cached branch must paginate."""
    monkeypatch.setenv("MEGABRAIN_RENDER_BUDGET", "2000")
    body = "\n".join(f"line {i} {'x' * 50}" for i in range(200))
    out = {"query": "q", "repo": "r", "retrieval_ms": 1,
           "served_from_cache": True, "text": body}
    p1 = render_ask(out, page=1)
    p2 = render_ask(dict(out), page=2)
    assert "page 1/" in p1 and "flow-cached" in p1
    assert "line 0 " in p1 and "line 0 " not in p2
    assert p2.splitlines()[1].startswith("repo `r` · ⚡ served from flow cache")


def test_page_out_of_range_clamps(monkeypatch):
    monkeypatch.setenv("MEGABRAIN_RENDER_BUDGET", "8000")
    last = render_ask(_big_ask(), page=99)
    assert "Do not act on partial evidence" not in last   # clamped to last page
