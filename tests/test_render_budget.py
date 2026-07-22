"""The pruned render's output budget: bodies degrade to span pointers, files
never disappear. Field case (click#3362 run): one scoped question rendered
90KB, overflowed the MCP host's inline limit, and the agent worked from a 2KB
preview — an unread answer. Completeness is CHUNK-LIST completeness; the
budget spends bodies top-down by rank and says what it omitted."""

from megabrain.retrieval.render import CHUNK_LINE_CAP, render_pruned


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
    # rank 1 gets its body; the tail gets pointers
    assert "line 0" in out
    assert out.count("body omitted") >= 3
    assert "Read src/f6.py:L1-50" in out
    assert "output budget" in out.splitlines()[2] or "output budget" in out


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


def test_single_oversized_chunk_is_line_capped_with_pointer():
    """No query overlap -> the head window, the pre-existing behavior."""
    res = _res(n=1, body_lines=CHUNK_LINE_CAP + 40)
    out = render_pruned(res, budget=100_000)
    assert f"line {CHUNK_LINE_CAP - 1}" in out       # cap-1 shown
    assert f"line {CHUNK_LINE_CAP}\n" not in out     # cap hidden
    assert f"+40 lines — Read src/f1.py:L{1 + CHUNK_LINE_CAP}-" in out


def test_cap_window_follows_the_query_not_the_head():
    """Field report deduction: a 180-line chunk whose relevant method sits
    deep inside rendered its head — exactly the part the agent didn't need.
    The cap window must center on the query-matching lines, with BOTH
    omitted sides pointed at."""
    n_lines = CHUNK_LINE_CAP + 120
    lines = [f"filler {i}" for i in range(n_lines)]
    lines[150] = "def write_usage_wrapping(self, breakpoints):"
    res = _res(n=1, body_lines=1)
    res["chunks"][0]["text"] = "\n".join(lines)
    res["chunks"][0]["end_line"] = n_lines
    res["query"] = "how does write_usage_wrapping choose breakpoints"
    out = render_pruned(res, budget=100_000)
    assert "write_usage_wrapping" in out             # the window found it
    assert "filler 0" not in out                     # head not shown
    assert "lines above — Read src/f1.py:L1-" in out
    assert "— Read src/f1.py:L" in out


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
