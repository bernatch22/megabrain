"""megabrain grep — literal search resolved against the index: matches
classified by role (defines/reads/config/tests/docs), reads ranked by
in-degree, incoming edges surfaced. Zero LLM, zero vectors."""

import pytest

from megabrain.retrieval.grepx import grep_repo, render_grep


@pytest.fixture
def grep_repo_fs(tmp_path, fake_embedder):
    """A repo shaped to exercise every section: a defining module, two
    readers (one heavily imported, one leaf), a test, and a doc."""
    (tmp_path / "core.py").write_text(
        'MAX_RETRIES = 3\n\n\n'
        'def resolve_flag(config):\n'
        '    """Read the analyze_files flag from config."""\n'
        '    return config.get("analyze_files", True)\n')
    (tmp_path / "engine.py").write_text(
        'from core import resolve_flag\n\n\n'
        'def build(config):\n'
        '    """Build honoring the flag."""\n'
        '    if resolve_flag(config):\n'
        '        return "full"\n'
        '    return "shallow"\n')
    (tmp_path / "mid.py").write_text(
        'from core import resolve_flag\n\n\n'
        'def probe(config):\n'
        '    """Probe the flag."""\n'
        '    return resolve_flag(config)\n')
    (tmp_path / "leaf.py").write_text(
        'from engine import build\n'
        'from mid import probe\n\n\n'
        'def run(config):\n'
        '    """Entry point calling build."""\n'
        '    return build(config) if probe(config) else None\n')
    (tmp_path / "test_engine.py").write_text(
        'from engine import build\n\n\n'
        'def test_build_honors_resolve_flag():\n'
        '    assert build({"analyze_files": False}) == "shallow"\n')
    (tmp_path / "README.md").write_text(
        '# demo\n\nSet analyze_files to control resolve_flag behavior.\n')
    from megabrain.indexing.indexer import index_repo
    index_repo(tmp_path)
    return tmp_path


def test_roles_defines_reads_tests_docs(grep_repo_fs):
    res = grep_repo(grep_repo_fs, "resolve_flag")
    files = lambda sec: [m["file"] for m in res[sec]]  # noqa: E731
    # the def site of the symbol named like the pattern
    assert files("defines") == ["core.py"]
    assert res["defines"][0]["symbol"] == "resolve_flag"
    # real code reading it (engine.py: import line at module level -> config,
    # the call inside build() -> reads)
    assert "engine.py" in files("reads")
    # the test file never lands in reads/defines, whatever it contains
    assert files("tests") and all(f == "test_engine.py" for f in files("tests"))
    assert files("docs") == ["README.md"]
    assert res["matches"] == sum(len(res[k]) for k in
                                 ("defines", "reads", "config", "tests", "docs"))


def test_reads_ranked_by_in_degree(grep_repo_fs):
    """Two files read resolve_flag: engine.py (imported by leaf AND the test)
    and mid.py (imported by leaf only). The site more of the repo depends on
    ranks first."""
    res = grep_repo(grep_repo_fs, "resolve_flag")
    read_files = [m["file"] for m in res["reads"]]
    assert set(read_files) >= {"engine.py", "mid.py"}
    assert read_files.index("engine.py") < read_files.index("mid.py")
    by_file = {m["file"]: m["in_deg"] for m in res["reads"]}
    assert by_file["engine.py"] > by_file["mid.py"]


def test_reached_from_lists_incoming_edges(grep_repo_fs):
    res = grep_repo(grep_repo_fs, "resolve_flag")
    d = res["defines"][0]
    assert "engine.py" in d["reached_from"]    # engine imports core


def test_absent_caller_is_visible(grep_repo_fs):
    """The nx#35656 shape: leaf.py calls build() but never resolve_flag —
    so leaf.py must NOT appear in core.py's reached_from. That absence is
    the finding the tool exists to surface."""
    res = grep_repo(grep_repo_fs, "resolve_flag")
    assert "leaf.py" not in res["defines"][0]["reached_from"]


def test_literal_default_regex_optin(grep_repo_fs):
    assert grep_repo(grep_repo_fs, "config.get(")["matches"] == 1   # ( is literal
    rx = grep_repo(grep_repo_fs, r"resolve_\w+", regex=True)
    assert rx["matches"] >= 3
    assert grep_repo(grep_repo_fs, "RESOLVE_FLAG")["matches"] == 0
    assert grep_repo(grep_repo_fs, "RESOLVE_FLAG",
                     ignore_case=True)["matches"] >= 3


def test_path_filter_scopes(grep_repo_fs):
    res = grep_repo(grep_repo_fs, "resolve_flag", path_filter="engine.py")
    assert {m["file"] for sec in ("defines", "reads", "config", "tests", "docs")
            for m in res[sec]} == {"engine.py"}


def test_render_sections_and_zero(grep_repo_fs):
    out = render_grep(grep_repo(grep_repo_fs, "resolve_flag"))
    assert "DEFINES (1)" in out
    assert "← reached from: engine.py" in out
    assert "TESTS" in out and "DOCS" in out
    empty = render_grep(grep_repo(grep_repo_fs, "no_such_identifier_anywhere"))
    # zero is often THE answer (a flag nobody sets inherits the default):
    # stated as verified absence, never as self-doubt — with the honest scope
    assert "0 match(es)" in empty and "verified absence" in empty
    assert "index fresh" not in empty
    assert "lockfiles" in empty                    # the not-covered caveat


def test_render_caps_are_loud(grep_repo_fs, monkeypatch):
    import megabrain.retrieval.grepx as gx
    monkeypatch.setattr(gx, "MAX_PER_SECTION", 1)
    out = render_grep(grep_repo(grep_repo_fs, "build"))
    assert "more (narrow with" in out          # overflow counted, never silent


def test_payload_keeps_records_and_true_counts(grep_repo_fs):
    """The API view (studio) and the text view (CLI/MCP) are two renderings of
    ONE result: the payload keeps the sections as records so a UI can lay them
    out, and reports the true totals so a capped list never reads as complete."""
    from megabrain.retrieval.grepx import grep_payload
    res = grep_repo(grep_repo_fs, "resolve_flag")
    p = grep_payload(res, limit=1)
    assert p["pattern"] == "resolve_flag" and p["matches"] == res["matches"]
    assert p["counts"]["reads"] == len(res["reads"])       # the TRUE total…
    assert len(p["reads"]) == 1 and p["limit"] == 1        # …not what was sent
    assert p["defines"][0]["symbol"] == res["defines"][0]["symbol"]
    assert "reached_from" in p["defines"][0]


def test_mcp_tool_registered_and_dispatches(grep_repo_fs):
    from megabrain.server import mcp
    assert any(t["name"] == "megabrain_grep" for t in mcp.TOOLS)
    assert "megabrain_grep" in mcp.INSTRUCTIONS
    out = mcp.call_tool("megabrain_grep",
                        {"repo_path": str(grep_repo_fs),
                         "pattern": "resolve_flag"})
    assert "DEFINES (1)" in out and "core.py" in out


def test_render_compacts_tests_docs_to_one_line_per_file(grep_repo_fs):
    """click field run: 'multiple=True' rendered 26 quoted test lines — a
    wall. Tests/docs/config matter as LOCATIONS: one line per file with the
    count and line numbers, never the quoted bodies."""
    out = render_grep(grep_repo(grep_repo_fs, "resolve_flag"))
    tests_sec = out.split("TESTS (")[1].split("\n\n")[0]
    assert "×" in tests_sec and "L" in tests_sec
    assert "test_build_honors" not in tests_sec      # no quoted line bodies


def test_render_prints_each_reached_from_list_once(grep_repo_fs):
    """The same reached-from list printed verbatim under every match of the
    same core module (click field run: 20 repeats) — each distinct list
    renders once; repeats add zero information."""
    out = render_grep(grep_repo(grep_repo_fs, "resolve_flag"))
    import re as _re
    arrows = _re.findall(r"← reached from: (.+)", out)
    assert len(arrows) == len(set(arrows))           # no duplicate lists
