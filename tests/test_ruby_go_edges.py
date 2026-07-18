"""Ruby require graph + Go import/package graph (indexing/graph.py).

Fixtures mirror the shapes of the real repos the feature was built against:
sinatra (monorepo with sub-gems under */lib, autoload-wired rack-protection)
and gin (one big root package + sub-packages imported by module path)."""

import megabrain.indexing.strategies as strategies_mod
from megabrain.indexing.graph import (
    go_edges,
    go_package_index,
    ruby_edges,
)
from megabrain.indexing.indexer import index_repo
from megabrain.indexing.strategies import GoStrategy, RubyStrategy, build_registry
from megabrain.storage.store import Store

# ---------------------------------------------------------------- Ruby

RB_FILES = {
    "lib/sinatra.rb",
    "lib/sinatra/base.rb",
    "lib/sinatra/indifferent_hash.rb",
    "lib/sinatra/middleware/logger.rb",
    "rack-protection/lib/rack/protection.rb",
    "rack-protection/lib/rack/protection/xss_header.rb",
    "test/base_test.rb",
    "test/test_helper.rb",
}


def test_ruby_require_load_path_and_relative():
    src = (
        "require 'sinatra/indifferent_hash'\n"     # -> repo-root lib/
        "require 'rack/protection'\n"              # -> sub-gem */lib/
        "require_relative 'middleware/logger'\n"   # -> exact, relative
        "require 'json'\n"                         # stdlib: no edge
    )
    edges = dict(ruby_edges("lib/sinatra/base.rb", src, RB_FILES))
    assert edges == {
        "lib/sinatra/indifferent_hash.rb": "import",
        "rack-protection/lib/rack/protection.rb": "import",
        "lib/sinatra/middleware/logger.rb": "import",
    }


def test_ruby_autoload_and_own_dir_require():
    src = "autoload :XSSHeader, 'rack/protection/xss_header'\n"
    edges = ruby_edges("rack-protection/lib/rack/protection.rb", src, RB_FILES)
    assert edges == [("rack-protection/lib/rack/protection/xss_header.rb", "import")]
    # tests add their own dir to $LOAD_PATH: `require 'test_helper'` resolves
    # to the sibling, and a dotted-up require_relative escapes the dir
    src = "require 'test_helper'\nrequire_relative '../lib/sinatra/base'\n"
    edges = dict(ruby_edges("test/base_test.rb", src, RB_FILES))
    assert edges == {"test/test_helper.rb": "import",
                     "lib/sinatra/base.rb": "import"}


def test_ruby_never_self_edges_and_unresolved_is_silent():
    src = "require 'sinatra/base'\nrequire 'nonexistent/thing'\n"
    assert ruby_edges("lib/sinatra/base.rb", src, RB_FILES) == []


# ---------------------------------------------------------------- Go

GO_SOURCES = {
    "gin.go": (
        "package gin\n\n"
        "type Engine struct{}\n\n"
        "func New() *Engine { return &Engine{} }\n"
    ),
    "context.go": (
        "package gin\n\n"
        'import "github.com/gin-gonic/gin/binding"\n\n'
        "type Context struct{ engine *Engine }\n\n"    # sibling use: Engine
        "func (c *Context) MustBind() { binding.Default() }\n"
    ),
    "binding/binding.go": (
        "package binding\n\n"
        "const (\n\tMIMEJSON = \"application/json\"\n)\n\n"
        "func Default() {}\n"
    ),
    "binding/json.go": (
        "package binding\n\n"
        "func decode() { _ = MIMEJSON }\n"             # sibling use via const block
    ),
    "gin_test.go": (
        "package gin_test\n\n"
        'import "github.com/gin-gonic/gin"\n\n'        # module ROOT by pkg name
        "func TestNew() { gin.New() }\n"
    ),
}


def test_go_package_index_top_level_only():
    ctx = go_package_index(GO_SOURCES)
    assert ctx["decls"][("", "gin")] == {"Engine": "gin.go", "New": "gin.go",
                                         "Context": "context.go"}
    # methods (MustBind) never enter the map; const-block names do
    assert ctx["decls"][("binding", "binding")] == {
        "MIMEJSON": "binding/binding.go", "Default": "binding/binding.go",
        "decode": "binding/json.go"}
    assert ctx["dirs"][""] == {"gin", "gin_test"}


def test_go_import_lane_pins_the_defining_file():
    ctx = go_package_index(GO_SOURCES)
    edges = dict(go_edges("context.go", GO_SOURCES["context.go"], ctx))
    # binding.Default() -> the file DEFINING Default; Engine -> sibling lane
    assert edges == {"binding/binding.go": "import", "gin.go": "call"}
    # stdlib imports never edge
    src = 'package gin\n\nimport "net/http"\n\nfunc f() { http.Get("x") }\n'
    assert go_edges("other.go", src, ctx) == []


def test_go_module_root_resolves_by_package_name():
    ctx = go_package_index(GO_SOURCES)
    edges = go_edges("gin_test.go", GO_SOURCES["gin_test.go"], ctx)
    assert ("gin.go", "import") in edges           # gin.New() -> its def file


def test_go_sibling_lane_ignores_dotted_and_string_uses():
    ctx = go_package_index(GO_SOURCES)
    src = (
        "package gin\n\n"
        'var s = "a New day for the Engine"\n'      # in a string: no edge
        "func g() { other.New() }\n"                # dotted: not a sibling use
    )
    assert go_edges("noise.go", src, ctx) == []
    assert dict(go_edges("binding/json.go", GO_SOURCES["binding/json.go"], ctx)) \
        == {"binding/binding.go": "call"}


# ---------------------------------------------------------------- wiring

def test_strategies_wired_into_registry():
    reg = build_registry("demo")
    by_ext = {e: s for s in reg for e in s.exts}
    assert isinstance(by_ext[".rb"], RubyStrategy)
    assert isinstance(by_ext[".go"], GoStrategy)
    rb, go = by_ext[".rb"], by_ext[".go"]
    ctx = rb.build_edge_ctx({f: "" for f in RB_FILES}, "demo")
    assert rb.extract_edges("test/base_test.rb", "require 'test_helper'\n", ctx) \
        == [("test/test_helper.rb", "import")]
    gctx = go.build_edge_ctx(GO_SOURCES, "demo")
    assert go.extract_edges("context.go", GO_SOURCES["context.go"], gctx)


# ------------------------------------------------- edge-schema self-healing

def test_edge_schema_bump_regraphs_untouched_files(tmp_path, fake_embedder,
                                                   monkeypatch):
    """The bernardocastro.dev bug: a repo indexed by an engine WITHOUT the
    extractor kept an empty graph forever, because edges only re-extract for
    sha-changed files. A schema bump must re-graph untouched files — and must
    NOT re-embed them (that's the expensive path this avoids)."""
    (tmp_path / "a.rb").write_text("require_relative 'b'\n")
    (tmp_path / "b.rb").write_text("class B; end\n")

    # index as an engine that has no Ruby extractor
    real_extract = RubyStrategy.extract_edges
    monkeypatch.setattr(RubyStrategy, "extract_edges",
                        lambda self, rel, src, ctx: None)
    index_repo(tmp_path)
    with Store(tmp_path) as s:
        assert s.db.execute("SELECT count(*) FROM edges").fetchone()[0] == 0

    # upgrade: the real extractor is back and the schema moved. (Restore the
    # method directly — monkeypatch.undo() would also revert fake_embedder,
    # whose fixture shares this monkeypatch instance, and the resulting
    # embed-model change would force a full re-index and mask the bug.)
    monkeypatch.setattr(RubyStrategy, "extract_edges", real_extract)
    monkeypatch.setattr(strategies_mod, "EDGE_SCHEMA",
                        strategies_mod.EDGE_SCHEMA + 1)
    import megabrain.indexing.indexer as indexer_mod
    monkeypatch.setattr(indexer_mod, "EDGE_SCHEMA", strategies_mod.EDGE_SCHEMA)

    stats = index_repo(tmp_path)          # incremental: no file changed
    assert stats["changed"] == 0 and stats["regraphed"] == 2
    assert stats["new_chunks"] == 0       # nothing re-embedded
    with Store(tmp_path) as s:
        assert s.db.execute("SELECT src,dst,kind FROM edges").fetchall() \
            == [("a.rb", "b.rb", "import")]

    stats = index_repo(tmp_path)          # schema now current: no repeat work
    assert stats["regraphed"] == 0
