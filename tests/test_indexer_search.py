"""End-to-end indexer + store + search on a tmp repo with the fake embedder.
No network, no corpus — this is what contributors/CI can always run."""

from megabrain.indexer import index_repo
from megabrain.query import search
from megabrain.store import Store


def _bundle_files(res):
    return [t["file"] for t in res["tier1"]] + [t["file"] for t in res["tier2"]]


# ---------------------------------------------------------------- indexing

def test_index_counts_and_incremental(tiny_repo):
    st = Store(tiny_repo)
    assert st.get_meta("repo_name") == tiny_repo.name
    assert st.get_meta("embed_model")            # recorded for drift detection
    r2 = index_repo(tiny_repo, quiet=True)       # nothing changed
    assert r2["changed"] == 0 and r2["unchanged"] == 3


def test_force_reembeds_everything(tiny_repo):
    r = index_repo(tiny_repo, quiet=True, force=True)
    assert r["changed"] == 3 and r["unchanged"] == 0


def test_embed_model_change_triggers_full_reembed(tiny_repo, monkeypatch):
    import megabrain.indexer as indexer
    monkeypatch.setattr(indexer, "EMBED_MODEL", "other/model")
    r = index_repo(tiny_repo, quiet=True)        # no force asked — auto-detected
    assert r["changed"] == 3
    assert Store(tiny_repo).get_meta("embed_model") == "other/model"


def test_orphan_files_are_pruned(tiny_repo):
    (tiny_repo / "util.py").unlink()
    r = index_repo(tiny_repo, quiet=True)
    assert r["removed"] == 1
    assert "util.py" not in Store(tiny_repo).all_paths()


def test_changed_file_reindexed_alone(tiny_repo):
    p = tiny_repo / "util.py"
    p.write_text(p.read_text() + "\n\ndef extra():\n    return 1\n")
    r = index_repo(tiny_repo, quiet=True)
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
    from megabrain.indexer import _excluded, _split_patterns
    names, globs = _split_patterns(["migrations", "src/generated/", "*.pb.go", ""])
    assert names == {"migrations"} and set(globs) == {"src/generated", "*.pb.go"}
    assert _excluded("app/migrations/001.py", names, globs)        # bare name, any segment
    assert _excluded("src/generated/api.ts", names, globs)         # path prefix
    assert _excluded("src/generated", names, globs)                # the dir itself
    assert _excluded("pkg/user.pb.go", names, globs)               # glob on relpath
    assert not _excluded("src/app/main.py", names, globs)
    assert not _excluded("src/migrations_helper.py", names, globs) # not a full segment


def test_load_ignore_file(tmp_path):
    from megabrain.indexer import load_ignore
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
    from megabrain.indexer import discover, index_repo
    from megabrain.store import Store
    # discover() applies the patterns it's handed (+ built-ins):
    found = {p.relative_to(tmp_path).as_posix()
             for p in discover(tmp_path, (".py",), ["generated", "vendor"])}
    assert found == {"src/keep.py"}
    # index_repo() merges .megabrainignore (vendor) with the --exclude flag (generated):
    index_repo(tmp_path, quiet=True, exclude=["generated"])
    assert Store(tmp_path).all_paths() == {"src/keep.py"}
