"""serve-api route tests for the studio UI gaps — real HTTP on an ephemeral
port against the tiny_repo fixture (fake embedder, no network). Covers the new
routes added for megabrain studio: /repos, /providers, /scan, /prune, the
/index/stream progress SSE, /repos/add, and static UI serving."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from megabrain.server.http import Registry, _make_handler
from megabrain.server.session import RepoSession


@pytest.fixture
def server(tiny_repo):
    reg = Registry(RepoSession(tiny_repo))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(reg, None, True))
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, tiny_repo
    httpd.shutdown()


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, json.loads(r.read())


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read())


def _sse(base, path, body):
    """POST + read an event-stream to completion → list of parsed events."""
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    events = []
    with urllib.request.urlopen(req, timeout=30) as r:
        frame = []
        for raw in r:
            line = raw.decode().rstrip("\n")
            if line == "":
                data = "".join(x[5:].strip() for x in frame if x.startswith("data:"))
                if data:
                    events.append(json.loads(data))
                frame = []
            else:
                frame.append(line)
    return events


def test_repos_lists_boot_repo(server):
    base, repo = server
    status, body = _get(base, "/repos")
    assert status == 200
    names = {r["name"] for r in body}
    assert repo.name in names
    me = next(r for r in body if r["name"] == repo.name)
    assert me["files"] >= 3 and me["chunks"] >= 1


def test_providers_shape(server):
    base, _ = server
    status, body = _get(base, "/providers")
    assert status == 200
    for k in ("claude", "openrouter", "ollama", "active"):
        assert k in body
    assert "available" in body["claude"]
    assert "up" in body["ollama"] and isinstance(body["ollama"]["models"], list)


def test_scan_census(server):
    base, repo = server
    status, body = _get(base, "/scan?path=" + str(repo))
    assert status == 200
    assert body["would_index"] >= 3
    assert ".py" in body["by_ext"]
    assert isinstance(body["flagged"], list)
    assert "proposed_ignore" in body
    # indexable paths drive the studio's add-repo tree
    assert isinstance(body["paths"], list) and len(body["paths"]) == body["would_index"]
    assert all("/" in p or p.endswith(".py") for p in body["paths"])


def test_scan_flags_gitignored(server):
    base, repo = server
    (repo / "secret.py").write_text("x = 1\n")
    (repo / ".gitignore").write_text("secret.py\n")
    status, body = _get(base, "/scan?path=" + str(repo))
    flagged = {f["path"]: f["reason"] for f in body["flagged"]}
    assert flagged.get("secret.py") == "gitignored"


def test_prune_signal_and_noise(server):
    base, _ = server
    status, body = _get(base, "/prune?q=how%20is%20a%20user%20login%20authenticated")
    assert status == 200
    assert "chunks" in body and "noise" in body
    assert body["kept"] == len(body["chunks"])
    assert set(("kept", "pruned", "scanned", "ms")) <= set(body)


def test_grep_returns_records_not_text(server):
    """The studio DRAWS grep, so the route answers with the role-grouped
    result as data — sections of records + true counts. (The CLI/MCP string
    view is render_grep over the same result; only the rendering differs.)"""
    status, body = _get(server[0], "/grep?q=check_password")
    assert status == 200
    assert body["pattern"] == "check_password" and body["matches"] >= 2
    assert body["counts"]["defines"] == len(body["defines"]) >= 1
    d = body["defines"][0]
    # the fields a UI lays out — a string view would have flattened these
    assert d["file"].endswith("login.py") and isinstance(d["line"], int)
    assert d["symbol"] == "check_password" and "in_deg" in d
    assert isinstance(d.get("reached_from"), list)


def test_grep_zero_is_a_stated_result(server):
    """0 matches is evidence, not an error: a well-formed empty payload with
    every section present, so the UI can say 'verified absence' honestly."""
    status, body = _get(server[0], "/grep?q=nonexistent_symbol_xyz")
    assert status == 200 and body["matches"] == 0 and body["files"] == 0
    assert all(body["counts"][k] == 0 for k in
               ("defines", "reads", "config", "tests", "docs"))


def test_grep_flags_and_bad_regex(server):
    base = server[0]
    assert _get(base, "/grep?q=CHECK_PASSWORD")[1]["matches"] == 0
    assert _get(base, "/grep?q=CHECK_PASSWORD&ignore_case=1")[1]["matches"] >= 2
    assert _get(base, "/grep?q=check_%5Cw%2B&regex=1")[1]["matches"] >= 2
    assert _get(base, "/grep?q=check_%5Cw%2B")[1]["matches"] == 0   # literal default
    # a half-typed regex from the UI is a 400 with a reason, never a 500
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base, "/grep?q=%28unclosed&regex=1")
    assert e.value.code == 400 and "invalid regex" in e.value.read().decode()


def test_search_repo_param(server):
    base, repo = server
    status, body = _post(base, "/search", {"query": "billing invoice", "repo": repo.name})
    assert status == 200
    assert body["tier1"], "expected at least one core file"


def test_index_stream_emits_progress(server):
    base, repo = server
    events = _sse(base, "/index/stream", {"repo": repo.name, "scan_filters": False})
    files = [e for e in events if e["type"] == "file"]
    done = [e for e in events if e["type"] == "done"]
    assert files, "expected per-file progress events"
    assert all("i" in e and "n" in e and "file" in e for e in files)
    assert files[-1]["i"] <= files[-1]["n"]
    assert done and done[0]["files"] >= 3


def test_repos_add_registers_and_writes_ignore(server, tmp_path, fake_embedder):
    base, _ = server
    other = tmp_path / "otherrepo"
    other.mkdir()
    (other / "m.py").write_text("def f():\n    return 1\n")
    status, body = _post(base, "/repos/add",
                         {"path": str(other), "ignore": "build/    # vendored"})
    assert status == 200
    assert body["name"] == "otherrepo"
    assert (other / ".megabrainignore").read_text().strip().startswith("build/")
    # it now shows up in /repos
    _, repos = _get(base, "/repos")
    assert "otherrepo" in {r["name"] for r in repos}


def test_static_ui_served(server):
    base, _ = server
    with urllib.request.urlopen(base + "/", timeout=10) as r:
        html = r.read().decode()
    assert r.status == 200
    assert "megabrain studio" in html.lower()
    # and the api layer is referenced
    assert "api.js" in html
    # sibling scripts serve at the top level (index.html links ./api.js etc.)
    for name in ("api.js", "app.js"):
        with urllib.request.urlopen(base + "/" + name, timeout=10) as rr:
            assert rr.status == 200
            assert "javascript" in (rr.headers.get("content-type") or "")


def test_providers_select_switches_active(server):
    base, _ = server
    import os

    from megabrain import providers
    try:
        _, d = _post(base, "/providers/select",
                     {"provider": "openrouter", "model": "qwen/qwen3-coder"})
        assert d["active"]["label"] == "openrouter"
        assert d["active"]["model"] == "qwen/qwen3-coder"
        _, d2 = _post(base, "/providers/select", {"provider": "ollama"})
        assert d2["active"]["label"] == "ollama"           # localhost chat base
    finally:                                                # don't leak switched env
        os.environ.pop("MEGABRAIN_ASK_MODEL", None)
        os.environ.pop("MEGABRAIN_CHAT_BASE_URL", None)
        providers.CHAT_BASE_URL = providers.BASE_URL


def test_repos_reports_embed_model(server):
    base, repo = server
    _, repos = _get(base, "/repos")
    me = next(r for r in repos if r["name"] == repo.name)
    assert me["embed_model"], "index should record which embedding it used"


def test_index_stream_switches_embedding(server):
    base, repo = server
    import os

    from megabrain import providers
    old_model = os.environ.get("MEGABRAIN_EMBED_MODEL")
    old_base = providers.EMBED_BASE_URL
    try:
        events = _sse(base, "/index/stream",
                      {"repo": repo.name, "embed_model": "fake/other-embed"})
        done = [e for e in events if e["type"] == "done"]
        assert done and done[0]["embed_model"] == "fake/other-embed"
        assert done[0]["changed"] >= 3, "model change must re-embed every file"
        # /repos now reflects the new embedding
        _, repos = _get(base, "/repos")
        me = next(r for r in repos if r["name"] == repo.name)
        assert me["embed_model"] == "fake/other-embed"
    finally:
        if old_model is None:
            os.environ.pop("MEGABRAIN_EMBED_MODEL", None)
        else:
            os.environ["MEGABRAIN_EMBED_MODEL"] = old_model
        providers.EMBED_BASE_URL = old_base


def test_repos_add_honors_tree_exclusions(server, tmp_path, fake_embedder):
    """The studio tree sends excluded paths as .megabrainignore lines; a
    re-index must then skip them — end-to-end proof the selector works."""
    base, _ = server
    repo = tmp_path / "sel"
    (repo / "keep").mkdir(parents=True)
    (repo / "drop").mkdir()
    (repo / "keep" / "a.py").write_text("def a():\n    return 1\n")
    (repo / "drop" / "b.py").write_text("def b():\n    return 2\n")
    # add with the tree having excluded the `drop/` folder
    _post(base, "/repos/add", {"path": str(repo), "ignore": "drop/"})
    events = _sse(base, "/index/stream", {"repo": "sel", "scan_filters": True})
    indexed = {e["file"] for e in events if e["type"] == "file"}
    assert any("keep/a.py" in f for f in indexed)
    assert not any("drop/b.py" in f for f in indexed)   # excluded by the tree


def test_prune_rerank_param(server, monkeypatch):
    """/prune?rerank=1 runs the LLM lane (here mocked) and annotates the result."""
    base, _ = server
    import megabrain.providers as providers
    monkeypatch.setattr(providers, "chat_text",
                        lambda model, prompt, max_tokens, **kw: "[]")
    # mocked model returns nothing usable -> fail-open, reranked: false
    status, body = _get(base, "/prune?q=user%20login&rerank=1")
    assert status == 200
    assert body["reranked"] is False
    assert body["kept"] == len(body["chunks"])


def test_repos_merges_global_registry(server, tmp_path):
    """/repos = warm sessions (loaded: true) + registry-only repos
    (loaded: false) — the studio rail shows both."""
    base, repo = server
    from megabrain.storage import registry
    other = tmp_path / "coldrepo"
    (other / ".megabrain").mkdir(parents=True)
    (other / ".megabrain" / "db.sqlite").write_bytes(b"")
    registry.register(other, {"files": 7, "chunks": 42, "embed_model": "m"})
    _, repos = _get(base, "/repos")
    by_name = {r["name"]: r for r in repos}
    assert by_name[repo.name]["loaded"] is True
    assert by_name["coldrepo"]["loaded"] is False
    assert by_name["coldrepo"]["chunks"] == 42


def test_repos_dedups_a_differently_spelled_root(tmp_path, monkeypatch):
    """The two sides of the /repos merge spell the same root differently: a
    warm session reports `str(root)` (native separators, as the session was
    constructed) while the registry stores `resolve().as_posix()`. On Windows
    that is `C:\\x\\y` vs `C:/x/y`, so the raw string compare never matched and
    the boot repo was listed TWICE — the cold copy landing last, reporting
    loaded:false. Reproduced on any OS with an unresolved session root."""
    from megabrain.server import http as http_mod
    from megabrain.storage import registry

    real = tmp_path / "myrepo"
    (real / ".megabrain").mkdir(parents=True)
    (real / ".megabrain" / "db.sqlite").write_bytes(b"")
    registry.register(real, {"files": 3, "chunks": 9, "embed_model": "m"})

    class _StubSession:                      # the session holds it unresolved
        root = tmp_path / "myrepo" / ".." / "myrepo"

        def with_state(self, fn):
            raise RuntimeError("not indexed")

    class _StubReg:
        def list(self):
            return [_StubSession()]

    monkeypatch.setattr(http_mod, "RepoSession", _StubSession, raising=False)
    out = http_mod._all_repos(_StubReg())
    mine = [r for r in out if Path(r["root"]).resolve() == real.resolve()]
    assert len(mine) == 1, f"root listed {len(mine)}x: {mine}"
    assert mine[0]["loaded"] is True


def test_graph_map_route(server, monkeypatch):
    base, _ = server
    import megabrain.providers as providers
    monkeypatch.setattr(providers, "chat_text",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")))
    status, body = _get(base, "/graph")
    assert status == 200
    assert body["files"] >= 3
    assert {"communities", "god_nodes", "surprises", "nodes", "links"} <= set(body)
    assert all(c["label"].startswith("Community") for c in body["communities"])


def test_graph_node_route(server, monkeypatch):
    base, _ = server
    import megabrain.providers as providers
    monkeypatch.setattr(providers, "chat_text",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")))
    status, body = _get(base, "/graph?mode=node&node=auth%2Flogin.py")
    assert status == 200
    assert body["file"] == "auth/login.py"
    assert body["chunks"] and "login" in body["chunks"][0]["text"]
    # missing node param -> 400
    import urllib.error
    with pytest.raises(urllib.error.HTTPError) as ei:
        _get(base, "/graph?mode=node")
    assert ei.value.code == 400


def test_graph_path_route(server, monkeypatch):
    base, _ = server
    import megabrain.providers as providers
    monkeypatch.setattr(providers, "chat_text",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")))
    status, body = _get(base, "/graph?mode=path&source=auth%2Flogin.py&target=util.py")
    assert status == 200
    assert "found" in body and isinstance(body["hops"], list)


def test_symbols_and_symbol_routes(server):
    """The code navigator's lookups: one file's outline, the repo-wide name
    index (which words become links), and go-to-definition by bare name."""
    base, _ = server
    status, body = _get(base, "/symbols?file=auth%2Flogin.py")
    assert status == 200
    names = {s["name"] for s in body["symbols"]}
    assert "login_user" in names and "check_password" in names
    # no file -> bare name -> definition count (the navigator's link policy)
    status, body = _get(base, "/symbols")
    assert status == 200
    assert body["names"]["login_user"] == 1 and body["names"]["check_password"] == 1
    status, body = _get(base, "/symbol?name=check_password")
    assert status == 200
    assert body["defs"] and body["defs"][0]["file"] == "auth/login.py"
    assert body["defs"][0]["line"] >= 1 and body["defs"][0]["kind"]
    import urllib.error
    with pytest.raises(urllib.error.HTTPError) as ei:
        _get(base, "/symbol")
    assert ei.value.code == 400


def test_search_signal_equals_prune_kept(server):
    """The Search stats line claims signal = core chunks + one best chunk per
    related file. That must be exactly what Prune reports as kept."""
    base, _ = server
    q = "user login password check"
    _, s = _post(base, "/search", {"query": q})
    core = sum(len(t["chunks"]) for t in s["tier1"])
    _, p = _get(base, "/prune?q=" + urllib.parse.quote(q))
    assert p["kept"] == core + len(s["tier2"])


@pytest.fixture
def ro_server(tiny_repo):
    """A public-demo server: readonly + a 2-asks/hour rate limit."""
    reg = Registry(RepoSession(tiny_repo))
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        _make_handler(reg, None, True, readonly=True, rate_limit=2))
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, tiny_repo
    httpd.shutdown()


def test_config_route_defaults(server):
    base, _ = server
    status, body = _get(base, "/config")
    assert status == 200
    assert body["readonly"] is False and body["rate_limit"] is None
    assert body["version"]


def test_readonly_blocks_every_mutating_route(ro_server):
    """--readonly is a SERVER-side lock: each mutating/config route 403s
    regardless of what any UI shows."""
    import urllib.error
    base, repo = ro_server
    _, cfg = _get(base, "/config")
    assert cfg["readonly"] is True and cfg["rate_limit"] == 2
    for method, path, body in [
        ("GET", "/scan?path=" + str(repo), None),
        ("GET", "/fs/pick", None),
        ("POST", "/index", {}),
        ("POST", "/index/stream", {}),
        ("POST", "/repos/add", {"path": str(repo)}),
        ("POST", "/providers/select", {"provider": "openrouter"}),
        ("POST", "/providers/ollama/serve", {}),
        ("POST", "/flows/delete", {"id": 1}),
    ]:
        with pytest.raises(urllib.error.HTTPError) as ei:
            (_get(base, path) if method == "GET" else _post(base, path, body))
        assert ei.value.code == 403, f"{method} {path} must 403 in readonly"
    # ...while the read paths keep answering
    status, body = _post(base, "/search", {"query": "user login"})
    assert status == 200 and body["tier1"]
    status, body = _get(base, "/flows")
    assert status == 200 and "flows" in body


def test_rate_limit_meters_asks_only(ro_server):
    """The limiter counts /ask attempts per IP (2 here); retrieval stays
    unlimited. The 3rd ask 429s with a retry hint."""
    import urllib.error
    base, _ = ro_server
    for _i in range(2):                       # counted (400: missing question)
        with pytest.raises(urllib.error.HTTPError) as ei:
            _post(base, "/ask", {})
        assert ei.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as ei:
        _post(base, "/ask", {})
    assert ei.value.code == 429
    assert "rate limit" in json.loads(ei.value.read())["error"]
    for _i in range(4):                       # retrieval is never metered
        status, _ = _post(base, "/search", {"query": "billing"})
        assert status == 200


def test_unknown_repo_404(server):
    base, _ = server
    req = urllib.request.Request(base + "/search",
                                 data=json.dumps({"query": "x", "repo": "nope"}).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=10)
    assert ei.value.code == 404
