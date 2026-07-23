"""megabrain map — the structure card that replaces body-renders on implement
tasks: files ranked, match spans, RELEVANT symbol outline, edges both ways,
def sites, pinning tests. Never a code body (the host requires Read before
Edit — a body of an edit target is paid twice)."""

import pytest

from megabrain.retrieval.mapcard import map_repo, render_map


@pytest.fixture
def mapped(tiny_repo):
    return map_repo(tiny_repo, "how is a user login password checked")


def test_map_has_no_code_bodies(mapped):
    out = render_map(mapped)
    assert "```" not in out
    assert "def login_user(name, password):" not in out.replace(
        "def login_user(name, password)", "")  # signature ok, body never
    assert "return check_password" not in out                # body line
    assert "NO code bodies" in out


def test_map_ranks_files_with_spans_and_outline(mapped):
    files = [f["file"] for f in mapped["files"]]
    assert "auth/login.py" in files
    top = next(f for f in mapped["files"] if f["file"] == "auth/login.py")
    assert top["spans"] and top["spans"][0]["start_line"] >= 1
    sigs = " ".join(s["signature"] for s in top["outline"])
    assert "login_user" in sigs or "check_password" in sigs


def test_map_defines_lane_resolves_exact_identifiers(tiny_repo):
    res = map_repo(tiny_repo, "where is check_password defined")
    assert any(d["token"] == "check_password" and d["file"] == "auth/login.py"
               for d in res["defines"])


def test_map_render_is_grep_priced(mapped):
    assert len(render_map(mapped)) < 4000     # structure, not a dump


def test_map_trail_anchors_on_query_tokens_and_pre_runs_their_grep(tiny_repo):
    """The trail pins the ANCHOR symbols — those sharing a token with the
    query (login_user shares login+user) — and pre-runs each one's grep so
    no follow-up grep is needed. It ranks by shared tokens, so a fat chunk's
    unrelated neighbours (which share nothing) never lead."""
    res = map_repo(tiny_repo, "how is a user login verified")
    idents = [t["ident"] for t in res["trail"]]
    assert "login_user" in idents                 # anchor, shares login+user
    t = next(x for x in res["trail"] if x["ident"] == "login_user")
    assert t["defined"].startswith("auth/login.py:")
    out = render_map(res)
    assert "MECHANISM TRAIL" in out and "pre-run" in out


def test_map_trail_ranks_query_sharing_symbols_over_chunk_neighbours(tiny_repo):
    """A symbol sharing a query token outranks one that shares none — the
    fix for the jinja run where do_filesizeformat (a chunk neighbour of
    do_indent, sharing nothing) led the trail ahead of do_indent."""
    res = map_repo(tiny_repo, "login flow")
    if res["trail"]:
        assert res["trail"][0]["ident"] == "login_user"   # shares "login"


def test_defines_never_resolve_to_test_files(tmp_path, fake_embedder):
    """Field runs: 'method' resolved to tests/test_slots.py and 'function' to
    mypyc/test-data/fixtures — test files absorb generic names and slip past
    the ambiguity gate. DEFINES resolves against non-test symbols only."""
    (tmp_path / "core.py").write_text(
        'def process_order(order):\n'
        '    """Process an order end to end."""\n'
        '    return order\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_core.py").write_text(
        'def helper(x):\n'
        '    """Test helper for order checks."""\n'
        '    return x\n')
    from megabrain.indexing.indexer import index_repo
    index_repo(tmp_path)
    res = map_repo(tmp_path, "why does helper break process_order handling")
    assert any(d["token"] == "process_order" for d in res["defines"])
    assert not any("tests/" in d["file"] for d in res["defines"])


def test_map_keeps_a_flat_tail_past_the_file_cap(tiny_repo, monkeypatch):
    """mypy field run: scores near-tied across 13 files and the hard cut at
    MAX_FILES dropped solve.py/constraints.py — where the fix lived — while
    messages.py (which only FORMATS the symptom) topped the list. Files past
    the cap stay on the map as one-liners: file, span, symbol names."""
    from megabrain.retrieval import mapcard
    monkeypatch.setattr(mapcard, "MAX_FILES", 1)
    res = mapcard.map_repo(tiny_repo, "how is a user login password checked")
    assert res["files"][0]["file"] == "auth/login.py"
    assert res["tail"], "files past the cap must land in the tail"
    t = res["tail"][0]
    assert t["file"] != "auth/login.py" and t["span"].startswith("L")
    out = mapcard.render_map(res)
    assert "ALSO MATCHED" in out and t["file"] in out


def test_map_rerank_reorders_files_and_labels_judge_drops(tiny_repo, monkeypatch):
    """With rerank=True the judge's order drives the file ranking (mypy field
    run: messages.py, which only FORMATS the symptom, cosine-beat the
    constraint solver where the fix lived) and its drops land in the tail
    LABELED — structure, never deletion."""
    from megabrain.retrieval import mapcard, rerank

    def fake_rerank(res, q, model=None):
        ch = res["chunks"]
        first = [c for c in ch if "invoice" in c["file"]]
        rest = [c for c in ch if "invoice" not in c["file"]
                and "util" not in c["file"]]
        noise = [c for c in ch if "util" in c["file"]]
        res["chunks"] = first + rest
        res["noise"] = noise + res.get("noise", [])
        res["reranked"] = {"model": "fake-judge", "kept": len(first + rest),
                           "dropped": len(noise), "ms": 1}
        return res
    monkeypatch.setattr(rerank, "llm_rerank", fake_rerank)
    res = mapcard.map_repo(tiny_repo, "how is a user login password checked",
                           rerank=True)
    assert res["files"][0]["file"] == "billing/invoice.py"   # judge order wins
    assert res["judged"]["model"] == "fake-judge"
    dropped = [t for t in res["tail"] if t.get("judged_noise")]
    assert [t["file"] for t in dropped] == ["util.py"]
    out = mapcard.render_map(res)
    assert "judged by fake-judge" in out and "judged noise" in out
    assert "```" not in out                                  # still no bodies


def test_map_rerank_fails_open_to_deterministic_order(tiny_repo, monkeypatch):
    from megabrain.retrieval import mapcard, rerank

    def fake_rerank(res, q, model=None):
        res["reranked"] = False
        return res
    monkeypatch.setattr(rerank, "llm_rerank", fake_rerank)
    res = mapcard.map_repo(tiny_repo, "how is a user login password checked",
                           rerank=True)
    det = mapcard.map_repo(tiny_repo, "how is a user login password checked")
    assert [f["file"] for f in res["files"]] == [f["file"] for f in det["files"]]
    assert res["judged"] is None
    assert "judged by" not in mapcard.render_map(res)


def test_map_expand_widens_the_pool_with_llm_terms(tiny_repo, monkeypatch):
    """The judge can only reorder what cosine FOUND — expansion lets one
    cheap LLM call name the mechanism vocabulary the query lacks, and a
    second deterministic pass widens the pool. The LLM names search terms,
    never spans. A query about invoices that never says 'invoice' finds
    billing/invoice.py once the expander names it."""
    import megabrain.providers as providers
    from megabrain.retrieval import mapcard

    def fake_chat_text(model, prompt, max_tokens, **kw):
        assert "JSON array" in prompt
        return '["create_invoice", "billing amount"]'
    monkeypatch.setattr(providers, "chat_text", fake_chat_text)
    res = mapcard.map_repo(tiny_repo, "how is a user login password checked",
                           expand=True)
    assert res["expanded"]["terms"] == ["create_invoice", "billing amount"]
    assert any(f["file"] == "billing/invoice.py" for f in res["files"])
    out = mapcard.render_map(res)
    assert "expanded with mechanism terms: create_invoice" in out
    assert "```" not in out


def test_map_expand_fails_open(tiny_repo, monkeypatch):
    import megabrain.providers as providers
    from megabrain.retrieval import mapcard

    def broken_chat(model, prompt, max_tokens, **kw):
        raise TimeoutError("provider down")
    monkeypatch.setattr(providers, "chat_text", broken_chat)
    res = mapcard.map_repo(tiny_repo, "how is a user login password checked",
                           expand=True)
    det = mapcard.map_repo(tiny_repo, "how is a user login password checked")
    assert res["expanded"] is None
    assert [f["file"] for f in res["files"]] == [f["file"] for f in det["files"]]


def test_demo_files_never_make_the_head(tmp_path, fake_embedder):
    """attrs arena field run: typing-examples/baseline.py ranked #2 and fed
    the trail NGClass junk — demo/stub code shares the subsystem's vocabulary
    by DESIGN while implementing none of it. Demos drop to the tail labeled;
    the head and the trail stay implementation."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text(
        'def login_user(name, password):\n'
        '    """Authenticate a user login with password check."""\n'
        '    return bool(name and password)\n')
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "login_demo.py").write_text(
        'def demo_login_user():\n'
        '    """Example: authenticate a user login with password check."""\n'
        '    return "login_user(name, password) demo"\n')
    from megabrain.indexing.indexer import index_repo
    index_repo(tmp_path)
    res = map_repo(tmp_path, "how is a user login password checked")
    head = [f["file"] for f in res["files"]]
    assert "src/auth.py" in head
    assert "examples/login_demo.py" not in head
    demo = [t for t in res["tail"] if t.get("demo")]
    assert [t["file"] for t in demo] == ["examples/login_demo.py"]
    assert "example/stub" in render_map(res)
    assert all("demo_login_user" != t["ident"] for t in res["trail"])


def test_search_expand_widens_the_pool_and_renders_terms(tiny_repo, monkeypatch):
    """The expander is SHARED: app.prune(expand=True) runs the same PRF as
    map — the click field run needed a second search + greps because the
    first query lacked the mechanism vocabulary; the expander kills that
    follow-up round."""
    import megabrain.providers as providers
    from megabrain import app
    from megabrain.retrieval.render import render_pruned

    def fake_chat_text(model, prompt, max_tokens, **kw):
        assert "JSON array" in prompt
        return '["create_invoice", "billing amount"]'
    monkeypatch.setattr(providers, "chat_text", fake_chat_text)
    res = app.prune(tiny_repo, "how is a user login password checked",
                    expand=True)
    assert res["expanded"]["terms"] == ["create_invoice", "billing amount"]
    assert any(c["file"] == "billing/invoice.py" for c in res["chunks"])
    out = render_pruned(res)
    assert "expanded with mechanism terms: create_invoice" in out


def test_search_expand_fails_open(tiny_repo, monkeypatch):
    import megabrain.providers as providers
    from megabrain import app

    def broken_chat(model, prompt, max_tokens, **kw):
        raise TimeoutError("provider down")
    monkeypatch.setattr(providers, "chat_text", broken_chat)
    res = app.prune(tiny_repo, "how is a user login password checked",
                    expand=True)
    det = app.prune(tiny_repo, "how is a user login password checked")
    assert "expanded" not in res
    assert [c["id"] for c in res["chunks"]] == [c["id"] for c in det["chunks"]]


def test_defines_budget_prefers_specific_tokens(tiny_repo):
    """Field run: the agent put do_indent in the query and the generic words
    (indent, filter, first) consumed all 4 DEFINES slots, pushing out the one
    identifier that mattered. Specific tokens (underscored/camel, longer)
    spend the budget first, and a token that is a substring of a more
    specific one is dropped."""
    res = map_repo(tiny_repo, "how does login_user handle a user login")
    toks = [d["token"] for d in res["defines"]]
    assert "login_user" in toks
    assert "user" not in toks and "login" not in toks   # ride the specific one
