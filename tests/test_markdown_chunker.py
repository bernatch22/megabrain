"""Markdown chunker tests — same partition guarantee as code, QMD-style scored
break points. Run: python3 -m pytest tests/test_markdown_chunker.py -q"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from megabrain.chunker import nws, validate_partition
from megabrain.markdown import MarkdownChunker


def chunk(src, budget=4000, name="doc.md"):
    return MarkdownChunker(budget=budget, repo="test").chunk_file(name, src)


def test_empty_file():
    r = chunk("")
    assert r.chunks == [] and r.total_lines == 0


def test_no_heading_single_chunk():
    src = "just some prose\nover a couple of lines\nno headings at all\n"
    r = chunk(src)
    assert validate_partition(r) == []
    assert len(r.chunks) == 1
    assert r.chunks[0].kind == "doc"


def test_partition_full_coverage():
    src = ("# Title\n\nintro\n\n## A\n" + "alpha line\n" * 50 +
           "\n## B\n" + "beta line\n" * 50 + "\n## C\n" + "gamma\n" * 50)
    r = chunk(src, budget=400)
    assert validate_partition(r) == []
    assert len(r.chunks) > 1


def test_scored_cut_prefers_headings():
    """Several small sections (each < budget): cuts land on heading lines, not
    mid-section — the scored break point prefers headings over prose."""
    def sec(h):
        return f"## {h}\n" + "word word word word word word word word\n\n"
    src = "# Doc\n\n" + "".join(sec(f"S{i}") for i in range(10))
    r = chunk(src, budget=100)
    assert validate_partition(r) == []
    assert len(r.chunks) > 2
    # every chunk after the first starts on a heading line
    lines = src.splitlines()
    for c in r.chunks[1:]:
        assert lines[c.start_line - 1].startswith("#"), \
            f"chunk starts mid-section at L{c.start_line}: {lines[c.start_line-1]!r}"


def test_oversized_section_splits_within_budget():
    src = "## Big\n\n" + ("a sentence with several words here\n\n") * 80
    r = chunk(src, budget=600)
    assert validate_partition(r) == []
    assert len(r.chunks) > 1
    for c in r.chunks:
        # a single unbreakable line can exceed budget, but multi-line chunks
        # should be packed within budget + window
        if c.end_line > c.start_line:
            assert nws(c.text) <= 600 + 300


def test_never_cuts_inside_code_fence():
    code = "```python\n" + "x = 1\n" * 60 + "```\n"
    src = "# Doc\n\nintro paragraph\n\n" + code + "\nafter the block\n"
    r = chunk(src, budget=120)
    assert validate_partition(r) == []
    fence_open = next(i for i, ln in enumerate(src.splitlines(), 1) if ln.startswith("```"))
    fence_close = max(i for i, ln in enumerate(src.splitlines(), 1) if ln.startswith("```"))
    # no chunk boundary falls strictly inside the fenced block
    for c in r.chunks:
        assert not (fence_open < c.start_line <= fence_close), \
            f"chunk starts inside code fence at L{c.start_line}"


def test_frontmatter_in_first_chunk_and_breadcrumb():
    src = ("---\ntitle: My Guide\nslug: guide\n---\n\n# Heading\n\nbody text here\n")
    r = chunk(src)
    assert validate_partition(r) == []
    assert r.chunks[0].start_line == 1            # frontmatter stays in chunk 1
    assert "My Guide" in r.chunks[0].breadcrumb   # title folded into breadcrumb


def test_breadcrumb_tracks_heading_stack():
    src = ("# Top\n\nintro\n\n## Middle\n\nmid body\n\n### Leaf\n\nleaf body\n")
    r = chunk(src, budget=20)   # tiny budget -> each section its own chunk
    assert validate_partition(r) == []
    leaf = next(c for c in r.chunks if c.name and "Leaf" in c.name)
    assert "# Top" in leaf.breadcrumb and "## Middle" in leaf.breadcrumb \
        and "### Leaf" in leaf.breadcrumb


def test_headings_become_symbols():
    src = "# A\n\nx\n\n## B\n\ny\n\n## C\n\nz\n"
    r = chunk(src)
    names = [s.name for s in r.symbols]
    kinds = {s.kind for s in r.symbols}
    assert kinds == {"heading"}
    assert "A" in names and "A > B" in names and "A > C" in names
    assert "# A" in r.skeleton and "## B" in r.skeleton
