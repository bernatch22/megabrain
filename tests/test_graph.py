"""megabrain graph: structural+semantic build, deterministic communities,
god nodes, surprises, BFS paths, label caching — all on fixture repos with the
fake embedder (no network; labels fail open without a key)."""

from __future__ import annotations

import pytest

from megabrain import graph as G
from megabrain.retrieval.state import load_state


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """Community labeling must FAIL OPEN in tests, never hit the network (the
    dev machine may carry a real key). The cache test re-patches this."""
    import megabrain.providers as providers

    def _raise(*a, **k):
        raise RuntimeError("no llm in tests")
    monkeypatch.setattr(providers, "chat_text", _raise)


@pytest.fixture
def linked_repo(tmp_path, fake_embedder):
    """Two import-linked clusters + one semantic twin pair with no edge:

      web/server.py -> web/routes.py -> web/handlers.py   (cluster A)
      db/store.py  <-> db/models.py                       (cluster B)
      mailer.py · notifier.py   (same vocabulary, NO imports — semantic pair)
    """
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "server.py").write_text(
        "from web import routes\n\n\ndef serve_http():\n"
        '    """Start the http web server loop."""\n    return routes.dispatch()\n')
    (tmp_path / "web" / "routes.py").write_text(
        "from web import handlers\n\n\ndef dispatch():\n"
        '    """Dispatch an http route to its web handler."""\n'
        "    return handlers.handle()\n")
    (tmp_path / "web" / "handlers.py").write_text(
        "def handle():\n"
        '    """Handle one http web request."""\n    return 200\n')
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "store.py").write_text(
        "from db import models\n\n\ndef save_record(r):\n"
        '    """Persist a database record row."""\n    return models.Row(r)\n')
    (tmp_path / "db" / "models.py").write_text(
        "class Row:\n"
        '    """A database record row model."""\n'
        "    def __init__(self, r):\n        self.r = r\n")
    (tmp_path / "mailer.py").write_text(
        "def send_email_notification(user, message):\n"
        '    """Send an email notification message to the user inbox."""\n'
        "    return True\n")
    (tmp_path / "notifier.py").write_text(
        "def push_email_notification(user, message):\n"
        '    """Push an email notification message alert to the user inbox."""\n'
        "    return True\n")
    from megabrain.indexing.indexer import index_repo
    index_repo(tmp_path)
    return tmp_path


def test_build_graph_structural_edges(linked_repo):
    with load_state(linked_repo) as st:
        g = G.build_graph(st)
        i = g.idx["web/server.py"]
        assert g.idx["web/routes.py"] in g.struct[i]
        # db cluster is not linked to web cluster structurally
        assert g.idx["db/store.py"] not in g.struct[i]


def test_communities_are_deterministic_and_split_clusters(linked_repo):
    with load_state(linked_repo) as st:
        c1 = G.build_graph(st).comm
        c2 = G.build_graph(st).comm
    assert c1 == c2                                        # byte-stable
    # the web trio clusters together, db pair together, in different communities
    assert c1["web/server.py"] == c1["web/routes.py"] == c1["web/handlers.py"]
    assert c1["db/store.py"] == c1["db/models.py"]
    assert c1["web/server.py"] != c1["db/store.py"]


def test_god_nodes_ranked_by_degree(linked_repo):
    with load_state(linked_repo) as st:
        gods = G.god_nodes(G.build_graph(st))
    assert gods, "expected at least one god node"
    # routes.py touches server AND handlers -> degree 2, the max here
    assert gods[0]["file"] == "web/routes.py" and gods[0]["degree"] == 2


def test_semantic_pair_and_surprises(linked_repo):
    with load_state(linked_repo) as st:
        g = G.build_graph(st)
        i, j = g.idx["mailer.py"], g.idx["notifier.py"]
        assert j in g.sem[i], "near-identical files must get a semantic edge"
        # they may or may not land in different communities depending on the
        # semantic lane's pull; surprises() must at least not crash and every
        # surprise it reports must be structurally unlinked + cross-community
        for s in G.surprises(g):
            a, b = g.idx[s["a"]], g.idx[s["b"]]
            assert b not in g.struct[a]
            assert s["a_community"] != s["b_community"]


def test_shortest_path_follows_imports(linked_repo):
    with load_state(linked_repo) as st:
        g = G.build_graph(st)
        hops = G.shortest_path(g, "web/server.py", "web/handlers.py")
    assert [h["file"] for h in hops] == \
        ["web/server.py", "web/routes.py", "web/handlers.py"]
    assert hops[1]["via"] and all(k in ("import", "call")
                                  for k in hops[1]["via"].split("/"))


def test_resolve_node_path_and_concept(linked_repo):
    with load_state(linked_repo) as st:
        g = G.build_graph(st)
        assert G.resolve_node(st, g, "web/server.py") == "web/server.py"
        assert G.resolve_node(st, g, "server.py") == "web/server.py"   # basename
        # concept -> embedding lookup lands on the database cluster
        hit = G.resolve_node(st, g, "persist a database record row")
        assert hit in ("db/store.py", "db/models.py")


def test_graph_map_shape_and_fallback_labels(linked_repo):
    """No chat key in tests -> labeling fails open to 'Community N'."""
    res = G.graph_root(linked_repo, mode="map")
    assert res["files"] == 7
    assert {c["id"] for c in res["communities"]} == \
        {n["community"] for n in res["nodes"]}
    assert all(c["label"].startswith("Community") for c in res["communities"])
    kinds = {ln["kind"] for ln in res["links"]}
    assert kinds & {"import", "call", "import/call"}, "structural links present"
    assert "semantic" in kinds
    assert res["god_nodes"][0]["file"] == "web/routes.py"


def test_graph_node_splices_real_chunks(linked_repo):
    res = G.graph_root(linked_repo, mode="node", node="web/routes.py")
    assert res["file"] == "web/routes.py"
    outs = {e["file"] for e in res["out"]}
    ins = {e["file"] for e in res["in"]}
    assert "web/handlers.py" in outs and "web/server.py" in ins
    assert any("Dispatch an http route" in (c["text"] or "")
               for c in res["chunks"]), "real chunk text must be spliced"
    assert any(s["name"] == "dispatch" for s in res["symbols"])


def test_graph_path_mode_resolves_concepts(linked_repo):
    res = G.graph_root(linked_repo, mode="path",
                       source="server.py", target="handlers.py")
    assert res["found"] and len(res["hops"]) == 3


def test_graph_path_hops_carry_symbols(linked_repo):
    """Each hop names the functions/classes that connect the two files —
    server.py reaches routes.py via dispatch, routes.py reaches handlers.py
    via handle (the symbols table + chunk text, no new indexing)."""
    res = G.graph_root(linked_repo, mode="path",
                       source="web/server.py", target="web/handlers.py")
    assert "dispatch" in res["hops"][1]["symbols"]
    assert "handle" in res["hops"][2]["symbols"]
    assert "symbols" not in res["hops"][0]          # the start hop has no via
    # the walkthrough snippets: where the carrier is USED and where it's DEFINED
    code = res["hops"][1]["code"]
    assert code and code["symbol"] == "dispatch"
    assert code["def"]["file"] == "web/routes.py" and "def dispatch" in code["def"]["text"]
    assert code["use"]["file"] == "web/server.py" and "dispatch" in code["use"]["text"]
    assert code["use"]["hi"] == "dispatch" and code["use"]["start_line"] >= 1


def test_graph_path_not_found_between_islands(linked_repo):
    res = G.graph_root(linked_repo, mode="path",
                       source="web/server.py", target="db/models.py")
    # only reachable if a semantic edge bridges; either way the shape holds
    assert "found" in res and isinstance(res["hops"], list)
    if not res["found"]:
        assert res["hops"] == []


def test_label_cache_roundtrip(linked_repo, monkeypatch):
    """A successful labeling is cached under the graph fingerprint and reused
    without a second LLM call."""
    import megabrain.providers as providers
    calls = []

    def fake_chat(model, prompt, max_tokens, **kw):
        calls.append(1)
        return '{"0": "Web serving", "1": "Database rows", "2": "Email pings", "3": "x", "4": "y"}'
    monkeypatch.setattr(providers, "chat_text", fake_chat)
    with load_state(linked_repo) as st:
        g = G.build_graph(st)
        l1 = G.label_communities(st, g)
        l2 = G.label_communities(st, g)
    assert l1 == l2 and len(calls) == 1                   # second hit = cache
    assert l1[0] == "Web serving"


def test_mode_errors(linked_repo):
    from megabrain.errors import MegabrainError
    with pytest.raises(MegabrainError, match="needs `node`"):
        G.graph_root(linked_repo, mode="node")
    with pytest.raises(MegabrainError, match="source"):
        G.graph_root(linked_repo, mode="path", source="a")


def test_render_graph_all_modes(linked_repo):
    from megabrain.graph import render_graph
    assert "communities" not in render_graph(
        G.graph_root(linked_repo, mode="node", node="web/routes.py"))
    txt = render_graph(G.graph_root(linked_repo, mode="map"))
    assert "megabrain graph" in txt and "god nodes" in txt
    txt = render_graph(G.graph_root(linked_repo, mode="path",
                                    source="web/server.py",
                                    target="web/handlers.py"))
    assert "graph path" in txt
