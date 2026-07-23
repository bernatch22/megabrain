"""The pruned render's output budget: bodies degrade to span pointers, files
never disappear. Field case (click#3362 run): one scoped question rendered
90KB, overflowed the MCP host's inline limit, and the agent worked from a 2KB
preview — an unread answer. Completeness is CHUNK-LIST completeness; the
budget spends bodies top-down by rank and says what it omitted."""

from megabrain.retrieval.render import render_pruned


def _res(n=6, body_lines=50):
    body = "\n".join(f"line {i}" for i in range(body_lines))
    return {
        "query": "q", "repo": "r", "ms": 1, "kept": n, "pruned": 0,
        "chunks": [{"id": i, "file": f"src/f{i}.py", "start_line": 1,
                    "end_line": body_lines, "kind": "function", "name": f"fn{i}",
                    "score": 1.0 - i / 100, "text": body}
                   for i in range(1, n + 1)],
    }


def test_budget_degrades_bodies_never_spans():
    res = _res(n=6, body_lines=50)
    out = render_pruned(res, budget=1000)
    # every chunk's span line survives
    for i in range(1, 7):
        assert f"[{i}] src/f{i}.py" in out
    # rank 1 gets its body; the tail gets pointers in megabrain_read's spec
    # format, ready to paste into ONE batched read
    assert "line 0" in out
    assert out.count("body omitted") >= 3
    assert "megabrain_read src/f6.py:1-50" in out
    assert "batch the pointed specs in ONE megabrain_read" in out


def test_rank_order_is_the_spending_order():
    res = _res(n=3, body_lines=40)
    one_body = len(res["chunks"][0]["text"])
    out = render_pruned(res, budget=one_body + 10)
    # exactly the top-ranked body inline, the rest pointed
    assert out.index("```") < out.index("body omitted")
    assert out.count("```") == 2                     # one fenced block
    assert out.count("body omitted") == 2


def test_under_budget_output_is_unchanged():
    res = _res(n=2, body_lines=10)
    assert "body omitted" not in render_pruned(res, budget=100_000)
    assert "output budget" not in render_pruned(res, budget=100_000)


def test_a_body_that_fits_is_never_cut():
    """THE contract (field report, verbatim: 'me truncó justo lo que
    necesitaba… el costo de devolverlo entero era trivial'): no per-chunk
    cap exists — a 500-line body under budget renders whole."""
    res = _res(n=1, body_lines=500)
    out = render_pruned(res, budget=100_000)
    assert "line 0" in out and "line 499" in out     # first and last line
    assert "lines above" not in out and "body omitted" not in out
    assert "megabrain_read src/f1.py" not in out     # no pointers at all


def test_overflow_window_follows_the_query_not_the_head():
    """Only when a body does NOT fit the remaining budget: render the
    query-centered window that does, both omitted sides pointed at."""
    n_lines = 400
    lines = [f"filler {i} {'x' * 60}" for i in range(n_lines)]
    lines[300] = "def write_usage_wrapping(self, breakpoints):"
    res = _res(n=1, body_lines=1)
    res["chunks"][0]["text"] = "\n".join(lines)
    res["chunks"][0]["end_line"] = n_lines
    res["query"] = "how does write_usage_wrapping choose breakpoints"
    out = render_pruned(res, budget=6_000)           # body ~27KB: can't fit
    assert "write_usage_wrapping" in out             # the window found it
    assert "filler 0 " not in out                    # head not shown
    assert "lines above — megabrain_read src/f1.py:1-" in out
    assert "— megabrain_read src/f1.py:" in out


def test_tests_tail_survives_the_budget():
    """The spec tail is compact and must render even when every body was
    omitted — it was invisible in the overflowed 90KB output."""
    res = _res(n=4, body_lines=60)
    res["tests"] = [{"id": 99, "file": "tests/test_f.py",
                     "start_line": 1, "end_line": 30, "name": "test_fn"}]
    out = render_pruned(res, budget=200)
    assert "tests pinning this behavior" in out
    assert "[99] tests/test_f.py" in out


def test_pruned_audit_trail_renders_spans_only():
    """'112 pruned as noise' is unfalsifiable unless the pruned spans are
    visible. Top spans render one-per-line, rerank drops flagged, bodies
    never included."""
    res = _res(n=2, body_lines=5)
    res["pruned"] = 3
    res["noise_map"] = [
        {"file": "src/dropped.py", "start_line": 10, "end_line": 60,
         "score": 1.11, "rerank_drop": True},
        {"file": "src/faint.py", "start_line": 1, "end_line": 9, "score": 0.72},
    ]
    out = render_pruned(res, budget=100_000)
    assert "pruned, auditable (top 2 of 2" in out
    assert "src/dropped.py L10-60 · `1.11` · dropped by rerank" in out
    assert "src/faint.py L1-9 · `0.72`" in out
    # spans only — a pruned body never renders
    assert out.count("```") == 4              # the 2 signal chunks only


def test_seen_ids_dedup_repeats_across_calls():
    """click#3652 field run: three parallel searches over facets of one
    mechanism rendered the same get_help_record chunk THREE times. With a
    shared seen set, a body renders once; repeats become a one-line pointer
    and spend no budget."""
    seen: set = set()
    r1 = render_pruned(_res(n=2, body_lines=10), budget=100_000, seen_ids=seen)
    assert "line 0" in r1 and seen == {1, 2}
    r2 = render_pruned(_res(n=2, body_lines=10), budget=100_000, seen_ids=seen)
    assert "line 0" not in r2                       # body not repeated
    assert r2.count("already rendered in a previous result") == 2
    assert "megabrain_read src/f1.py:1-10" in r2    # pointer to refetch


def test_seen_ids_only_marks_whole_bodies():
    """A query-centered WINDOW is partial — it must not poison the seen set,
    or a later full render would degrade to a pointer."""
    seen: set = set()
    res = _res(n=1, body_lines=400)
    res["chunks"][0]["text"] = "\n".join(
        f"filler {i} {'x' * 60}" for i in range(400))
    res["chunks"][0]["end_line"] = 400
    render_pruned(res, budget=6_000, seen_ids=seen)   # window, not whole
    assert 1 not in seen
