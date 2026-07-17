"""mcp_server.py unit tests: tool schemas + scope resolution (no LLM calls)."""

from pathlib import Path

import pytest

from megabrain.server.mcp import TOOLS, _scope, call_tool


def test_tool_schemas_are_wellformed():
    names = [t["name"] for t in TOOLS]
    # deliberately lean: no get/chunks (the host has Read/Grep for single files)
    assert names == ["megabrain_ask", "megabrain_search", "megabrain_index",
                     "megabrain_forge", "megabrain_flows"]
    for t in TOOLS:
        req = t["inputSchema"].get("required", [])
        props = t["inputSchema"]["properties"]
        assert "repo_path" in props
        assert all(r in props for r in req)


def test_ask_and_search_expose_scope_path():
    for name in ("megabrain_ask", "megabrain_search"):
        t = next(t for t in TOOLS if t["name"] == name)
        assert "scope_path" in t["inputSchema"]["properties"]


def test_search_always_prunes_and_exposes_no_bundle_switch():
    """megabrain_search is signal-only, always: the code-less RELATED map was a
    dead end over MCP (no get/chunks tool to expand it). No prune_noise/full
    switch may come back — pruning already keeps every bundle file."""
    t = next(t for t in TOOLS if t["name"] == "megabrain_search")
    props = t["inputSchema"]["properties"]
    assert "prune_noise" not in props
    assert "full" not in props
    assert set(props) == {"repo_path", "task", "scope_path", "compact", "rerank"}


def test_search_takes_the_prune_path(monkeypatch):
    import megabrain.app as app
    import megabrain.server.mcp as mcp
    calls = []
    monkeypatch.setattr(app, "prune", lambda *a, **k: calls.append("prune") or {})
    monkeypatch.setattr(app, "query", lambda *a, **k: calls.append("query") or {})
    monkeypatch.setattr(mcp, "_scope", lambda args: (Path("/tmp"), None))
    monkeypatch.setattr("megabrain.retrieval.render.render_pruned", lambda *a, **k: "")

    mcp.call_tool("megabrain_search", {"repo_path": "/tmp", "task": "x"})
    assert calls == ["prune"]


def test_query_is_a_deprecated_dispatch_alias(monkeypatch):
    """0.9 clients still call megabrain_query — same prune path, not in TOOLS."""
    import megabrain.app as app
    import megabrain.server.mcp as mcp
    calls = []
    monkeypatch.setattr(app, "prune", lambda *a, **k: calls.append("prune") or {})
    monkeypatch.setattr(mcp, "_scope", lambda args: (Path("/tmp"), None))
    monkeypatch.setattr("megabrain.retrieval.render.render_pruned", lambda *a, **k: "")

    mcp.call_tool("megabrain_query", {"repo_path": "/tmp", "task": "x"})
    assert calls == ["prune"]
    assert "megabrain_query" not in [t["name"] for t in TOOLS]


def _fake_index(root):
    (root / ".megabrain").mkdir()
    (root / ".megabrain" / "db.sqlite").write_bytes(b"")


def test_scope_resolves_root_and_filter(tmp_path):
    _fake_index(tmp_path)
    (tmp_path / "src" / "dispatch").mkdir(parents=True)

    root, pf = _scope({"repo_path": str(tmp_path)})
    assert root == tmp_path and pf is None

    root, pf = _scope({"repo_path": str(tmp_path / "src" / "dispatch")})
    assert root == tmp_path and pf == "src/dispatch"

    root, pf = _scope({"repo_path": str(tmp_path), "scope_path": "src/dispatch"})
    assert root == tmp_path and pf == "src/dispatch"

    # back-compat alias
    root, pf = _scope({"repo_path": str(tmp_path), "subpath": "src"})
    assert root == tmp_path and pf == "src"


def test_scope_errors_without_index(tmp_path):
    with pytest.raises(ValueError, match="no megabrain index"):
        _scope({"repo_path": str(tmp_path)})


def test_unknown_tool_raises():
    with pytest.raises(ValueError, match="unknown tool"):
        call_tool("megabrain_nope", {})
