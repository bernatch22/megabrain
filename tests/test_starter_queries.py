"""Starter questions for the studio's Ask chips (app.example_queries).

EVERY indexed repo must produce some — a blank Ask box is the thing this
exists to prevent — so the three tiers (authored file > cached flows >
derived from the index) and their `source` labels are pinned here.
"""

from __future__ import annotations

import numpy as np

from megabrain import app
from megabrain.ask.warmup import central_files, derive_questions
from megabrain.storage.store import Store


def test_derived_questions_need_no_file_and_no_llm(tiny_repo):
    """The floor: a repo that authored nothing and was never asked still gets
    chips, with no LLM anywhere in the path."""
    out = app.example_queries(tiny_repo)
    assert out["source"] == "derived"
    assert out["queries"], "every indexed repo must offer starter questions"
    assert all(q.endswith("?") for q in out["queries"])
    # derived from the repo's real content, not a canned list
    joined = " ".join(out["queries"]).lower()
    assert any(w in joined for w in ("login", "invoice", "authenticate",
                                     "billing", "flatten", "util"))


def test_authored_file_wins_over_everything(tiny_repo):
    (tiny_repo / ".megabrainqueries").write_text(
        "# our main flows\nHow does login work?\n\nHow is an invoice created?\n",
        encoding="utf-8")
    out = app.example_queries(tiny_repo)
    assert out["source"] == "file"
    assert out["queries"] == ["How does login work?", "How is an invoice created?"]


def test_cached_flow_questions_are_the_fallback(tiny_repo):
    """Second tier: questions already in the flow cache. These are the best
    fallback precisely because their answers are cached — the chip serves
    instantly, so the UI advertises them as such."""
    with Store(tiny_repo) as s:
        v = np.ones(8, dtype=np.float32)
        s.insert_flow("How does the login flow work?", "…answer…",
                      {"auth/login.py": "deadbeef"}, v, v)
        s.commit()
    out = app.example_queries(tiny_repo)
    assert out["source"] == "flows"
    assert out["queries"] == ["How does the login flow work?"]


def test_ranking_survives_a_repo_with_no_import_graph(tiny_repo):
    """Ranking must not be edge-degree-only: the import/call graph covers
    py/ts/js/php, so a Go or Ruby repo has ~zero edges (measured on the demo
    box: ky 0, sinatra 1, gin 9) and degree-only ranking degenerates to
    arbitrary order. Symbol density has to carry those repos."""
    with Store(tiny_repo) as s:
        s.db.execute("DELETE FROM edges")
        s.commit()
    ranked = central_files(tiny_repo, 3)
    assert ranked, "no edges must still yield central files"
    files = [f for f, _ in ranked]
    # login.py defines the most symbols in this fixture -> it must lead
    assert files[0] == "auth/login.py", files
    assert all(label and not label.startswith("#") for _, label in ranked)


def test_side_paths_never_seed_a_question(tiny_repo):
    """Tests/examples describe the edges of a repo, not its main workflows."""
    (tiny_repo / "tests").mkdir(exist_ok=True)
    (tiny_repo / "tests" / "test_big.py").write_text(
        "".join(f'def test_{i}():\n    """Check case {i}."""\n    return {i}\n\n'
                for i in range(40)))          # by far the most symbols
    from megabrain.indexing.indexer import index_repo
    index_repo(tiny_repo, force=True)
    assert all("test" not in f for f, _ in central_files(tiny_repo, 3))


def test_limit_is_honored_and_questions_are_unique(tiny_repo):
    qs = derive_questions(tiny_repo, 2)
    assert len(qs) <= 2
    assert len(set(qs)) == len(qs)


# ── the languages that broke it in production ──────────────────────────────
# Python files carry a module docstring, so the label logic looked fine until
# it hit Go and TypeScript — which have none, leaving the skeleton's first
# line a raw declaration. Live output on the demo box before the fix:
#   "How does const ( work end to end?"
#   "How does var _ context.Context = (*Context)(nil) work end to end?"
#   "How does func TestRenderJSON(t *testing.T) work end to end?"

def test_a_declaration_is_never_mistaken_for_a_docline():
    from megabrain.ask.warmup import _is_prose
    for decl in ("const (", "var _ context.Context = (*Context)(nil)",
                 "func TestMappingBaseTypes(t *testing.T)", "type appkey struct",
                 "const maxErrorResponseBodySize = 10 * 1024 * 1024;",
                 "type InitHook = (options", "export function foo()"):
        assert not _is_prose(decl), decl
    for prose in ("SQLite storage: chunks, vectors, skeletons",
                  "Shared plumbing for the bakeoff scripts",
                  "The classic Ruby DSL for quick web apps"):
        assert _is_prose(prose), prose


def test_docline_less_language_falls_back_to_a_named_concept(tmp_path, fake_embedder):
    """Go: no module docstring, structs indexed as kind `type`. The label must
    be the concept the file defines, never its first constant or signature."""
    (tmp_path / "context.go").write_text(
        'package gin\n\n'
        'const AuthUserKey = "user"\n\n'
        'type Context struct {\n\tWriter int\n}\n\n'
        'func (c *Context) Next() {\n\tc.index++\n}\n\n'
        'func (c *Context) JSON(code int) {\n\tc.render(code)\n}\n')
    from megabrain.indexing.indexer import index_repo
    index_repo(tmp_path)
    label = dict(central_files(tmp_path, 5)).get("context.go") \
        or central_files(tmp_path, 5)[0][1]
    assert label == "Context", label
    q = derive_questions(tmp_path, 1)[0]
    assert q == "How does Context work end to end?", q


def test_same_package_test_files_are_excluded(tmp_path, fake_embedder):
    """Go/JS keep tests beside the code (`x_test.go`, `x.test.ts`), so a
    directory-only filter misses them — gin seeded questions off TestRenderJSON."""
    (tmp_path / "router.go").write_text(
        'package gin\n\ntype Engine struct {\n\tt int\n}\n')
    (tmp_path / "router_test.go").write_text(
        "".join(f'func TestCase{i}(t *testing.T) {{\n\treturn\n}}\n\n' for i in range(30)))
    (tmp_path / "helper.test.ts").write_text(
        "".join(f'export function spec{i}() {{ return {i}; }}\n' for i in range(30)))
    from megabrain.indexing.indexer import index_repo
    index_repo(tmp_path)
    picked = [f for f, _ in central_files(tmp_path, 5)]
    assert picked == ["router.go"], picked
