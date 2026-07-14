"""End-to-end indexer + store + search on a tmp repo with the fake embedder.
No network, no corpus — this is what contributors/CI can always run."""

from megabrain.indexing.indexer import index_repo
from megabrain.retrieval.bundle import search
from megabrain.store import Store


def _bundle_files(res):
    return [t["file"] for t in res["tier1"]] + [t["file"] for t in res["tier2"]]


# ---------------------------------------------------------------- indexing

def test_index_counts_and_incremental(tiny_repo):
    st = Store(tiny_repo)
    assert st.get_meta("repo_name") == tiny_repo.name
    assert st.get_meta("embed_model")            # recorded for drift detection
    r2 = index_repo(tiny_repo)       # nothing changed
    assert r2["changed"] == 0 and r2["unchanged"] == 3


def test_force_reembeds_everything(tiny_repo):
    r = index_repo(tiny_repo, force=True)
    assert r["changed"] == 3 and r["unchanged"] == 0


def test_embed_model_change_triggers_full_reembed(tiny_repo, monkeypatch):
    # construction-time config: the Embedder reads MEGABRAIN_EMBED_MODEL when
    # built, and index_repo trusts the instance's .model (no module globals)
    monkeypatch.setenv("MEGABRAIN_EMBED_MODEL", "other/model")
    r = index_repo(tiny_repo)        # no force asked — auto-detected
    assert r["changed"] == 3
    assert Store(tiny_repo).get_meta("embed_model") == "other/model"


def test_orphan_files_are_pruned(tiny_repo):
    (tiny_repo / "util.py").unlink()
    r = index_repo(tiny_repo)
    assert r["removed"] == 1
    assert "util.py" not in Store(tiny_repo).all_paths()


def test_changed_file_reindexed_alone(tiny_repo):
    p = tiny_repo / "util.py"
    p.write_text(p.read_text() + "\n\ndef extra():\n    return 1\n")
    r = index_repo(tiny_repo)
    assert r["changed"] == 1 and r["unchanged"] == 2


# ---------------------------------------------------------------- store

def test_store_roundtrip_symbols_and_chunks(tiny_repo):
    st = Store(tiny_repo)
    syms = st.symbols_for("auth/login.py")
    names = {s["name"] for s in syms}
    assert {"login_user", "check_password"} <= names
    metas, M = st.load_matrix()
    assert len(metas) == M.shape[0] > 0


# ---------------------------------------------------------------- search

def test_search_finds_the_relevant_file(tiny_repo):
    res = search(tiny_repo, "authenticate user login password")
    assert _bundle_files(res)[0] == "auth/login.py"


def test_prune_search_returns_flat_ranked_signal(tiny_repo):
    """pruneNoise: a flat list of only the SELECTED chunks, relevance-ordered,
    each with a stable id + span + code; noise counted, not returned."""
    from megabrain.retrieval.bundle import prune_search_root
    from megabrain.retrieval.render import render_pruned
    res = prune_search_root(tiny_repo, "authenticate user login password")
    ch = res["chunks"]
    assert ch, "must return signal chunks"
    # relevance-ordered, descending
    scores = [c["score"] for c in ch]
    assert scores == sorted(scores, reverse=True)
    # every item is a real chunk record with a stable id + span + code
    assert all({"id", "file", "start_line", "end_line", "score", "text"} <= c.keys()
               for c in ch)
    # ids are unique (dedup across tier1/tier2)
    assert len({c["id"] for c in ch}) == len(ch)
    # the top signal file is the relevant one, and counts are coherent
    assert ch[0]["file"] == "auth/login.py"
    assert res["kept"] == len(ch) and res["pruned"] >= 0
    assert res["scanned"] == res["kept"] + res["pruned"]
    # compact mode drops the code bodies
    lean = prune_search_root(tiny_repo, "authenticate user login password",
                             with_text=False)
    assert all("text" not in c for c in lean["chunks"])
    assert "signal chunks" in render_pruned(res)


def test_search_path_filter_scopes_bundle(tiny_repo):
    res = search(tiny_repo, "create invoice amount", path_filter="billing")
    files = _bundle_files(res)
    assert files and all(f.startswith("billing/") for f in files)


def test_search_root_equals_no_filter(tiny_repo):
    a = _bundle_files(search(tiny_repo, "flatten nested list"))
    b = _bundle_files(search(tiny_repo, "flatten nested list", path_filter=None))
    assert a == b


def test_reindex_preserves_incoming_edges(tiny_repo):
    """Regression: delete_file on re-index must NOT wipe A->B edges stored by an
    earlier-processed A when B is re-indexed later in the same pass."""
    st = Store(tiny_repo)
    st.replace_edges("auth/login.py", [("util.py", "import")])
    st.commit()
    st.delete_file("util.py")                       # re-index semantics
    rows = st.db.execute("SELECT src,dst FROM edges WHERE dst='util.py'").fetchall()
    assert rows == [("auth/login.py", "util.py")]
    st.delete_file("util.py", drop_incoming=True)   # orphan semantics
    rows = st.db.execute("SELECT src,dst FROM edges WHERE dst='util.py'").fetchall()
    assert rows == []


# ---------------------------------------------------------------- exclude / ignore

def test_split_and_match_patterns():
    from megabrain.indexing.indexer import _excluded, _split_patterns
    names, globs = _split_patterns(["migrations", "src/generated/", "*.pb.go", ""])
    assert names == {"migrations"} and set(globs) == {"src/generated", "*.pb.go"}
    assert _excluded("app/migrations/001.py", names, globs)        # bare name, any segment
    assert _excluded("src/generated/api.ts", names, globs)         # path prefix
    assert _excluded("src/generated", names, globs)                # the dir itself
    assert _excluded("pkg/user.pb.go", names, globs)               # glob on relpath
    assert not _excluded("src/app/main.py", names, globs)
    assert not _excluded("src/migrations_helper.py", names, globs) # not a full segment


def test_load_ignore_file(tmp_path):
    from megabrain.indexing.indexer import load_ignore
    (tmp_path / ".megabrainignore").write_text(
        "# comment\nvendor\n\nsrc/generated  # trailing note\n*.min.js\n")
    assert load_ignore(tmp_path) == ["vendor", "src/generated", "*.min.js"]


def test_discover_honors_exclude_and_ignorefile(tmp_path, fake_embedder):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "keep.py").write_text("def a():\n    return 1\n")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "skip.py").write_text("def g():\n    return 2\n")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "lib.py").write_text("def v():\n    return 3\n")
    (tmp_path / ".megabrainignore").write_text("vendor\n")
    from megabrain.indexing.indexer import discover, index_repo
    from megabrain.store import Store
    # discover() applies the patterns it's handed (+ built-ins):
    found = {p.relative_to(tmp_path).as_posix()
             for p in discover(tmp_path, (".py",), ["generated", "vendor"])}
    assert found == {"src/keep.py"}
    # index_repo() merges .megabrainignore (vendor) with the --exclude flag (generated):
    index_repo(tmp_path, exclude=["generated"])
    assert Store(tmp_path).all_paths() == {"src/keep.py"}


def test_is_test_path_detects_all_layouts():
    """Regression: the old detector checked only the SECOND path component and
    `tests/` plural, so `test/retry.ts` (ky, express) and `spec/…` (Ruby) never
    received TEST_PENALTY and outranked the core they exercise."""
    from megabrain.retrieval.scoring import _is_test_path

    assert _is_test_path("test/retry.ts")              # singular test/ dir
    assert _is_test_path("tests/foo.py")
    assert _is_test_path("spec/routing_spec.rb")       # ruby spec dir
    assert _is_test_path("pkg/__tests__/x.tsx")
    assert _is_test_path("a/b/testing/util.go")        # nested segment
    assert _is_test_path("lib/foo_test.go")            # filename token
    assert _is_test_path("test_client.py")
    assert _is_test_path("api.spec.ts")
    # never substring matches:
    assert not _is_test_path("src/contest/rank.py")
    assert not _is_test_path("lib/inspect.py")
    assert not _is_test_path("src/latest.js")
    assert not _is_test_path("protests/march.py")
