"""serve-api route tests for the studio UI gaps — real HTTP on an ephemeral
port against the tiny_repo fixture (fake embedder, no network). Covers the new
routes added for megabrain studio: /repos, /providers, /scan, /prune, the
/index/stream progress SSE, /repos/add, and static UI serving."""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

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


def test_unknown_repo_404(server):
    base, _ = server
    req = urllib.request.Request(base + "/search",
                                 data=json.dumps({"query": "x", "repo": "nope"}).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=10)
    assert ei.value.code == 404
