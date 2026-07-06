"""PHP chunker (tree-sitter LangSpec) — skipped when the grammar isn't installed."""

import pytest

pytest.importorskip("tree_sitter_php")

from megabrain.chunker import validate_partition  # noqa: E402
from megabrain.chunker_ts import PHP_SPEC, TreeSitterChunker  # noqa: E402
from megabrain.strategies import build_registry, strategy_for  # noqa: E402

SRC = '''<?php
namespace App\\Auth;

const MAX_TRIES = 3;

interface Guard {
    public function check(string $token): bool;
}

trait Loggable {
    public function log($msg) { error_log($msg); }
}

class LoginService implements Guard {
    private $repo;
    public function __construct($repo) { $this->repo = $repo; }
    public function check(string $token): bool {
        return $this->repo->valid($token);
    }
}

function make_token(int $len = 32): string {
    return bin2hex(random_bytes($len));
}

enum Status { case Open; case Closed; }
'''


def _result():
    return TreeSitterChunker(PHP_SPEC, repo="demo").chunk_file("src/Login.php", SRC)


def test_partition_is_clean():
    assert not validate_partition(_result())


def test_symbols_cover_all_defs_with_qualified_methods():
    names = {(s.kind, s.name) for s in _result().symbols}
    assert ("class", "LoginService") in names
    assert ("method", "LoginService.check") in names          # nested, qualified
    assert ("method", "LoginService.__construct") in names
    assert ("interface", "Guard") in names
    assert ("trait", "Loggable") in names
    assert ("enum", "Status") in names
    assert ("function", "make_token") in names
    assert ("namespace", "App\\Auth") in names
    assert ("const", "MAX_TRIES") in names


def test_skeleton_has_signatures():
    skel = _result().skeleton
    assert "class LoginService" in skel
    assert "function make_token" in skel


def test_registry_activates_php():
    reg = build_registry("x")
    assert strategy_for(reg, "src/a.php") is not None
    assert strategy_for(reg, "src/a.py") is not None          # sanity: py still there


def test_broken_php_falls_back_not_crash():
    r = TreeSitterChunker(PHP_SPEC).chunk_file("x.php", "<?php class {{{ broken")
    assert r.chunks                                            # degrades, never empty


# ---------------------------------------------------------------- import graph

def test_php_import_graph_resolves_use_statements():
    from megabrain.graph import php_class_index, php_edges
    sources = {
        "src/Controller/Shop.php": (
            "<?php\nnamespace App\\Controller;\n"
            "use App\\Repo\\Products;\nuse App\\Service\\{Cart, Pay as P};\n"
            "class Shop {}\n"),
        "src/Repo/Products.php": "<?php\nnamespace App\\Repo;\nclass Products {}\n",
        "src/Service/Cart.php": "<?php\nnamespace App\\Service;\nclass Cart {}\n",
        "src/Service/Pay.php": "<?php\nnamespace App\\Service;\ninterface Pay {}\n",
        "src/Other.php": "<?php\nnamespace App;\nuse function strlen;\nclass Other {}\n",
    }
    idx = php_class_index(sources)
    assert idx["App\\Repo\\Products"] == "src/Repo/Products.php"
    edges = dict(php_edges("src/Controller/Shop.php",
                           sources["src/Controller/Shop.php"], idx))
    assert edges == {"src/Repo/Products.php": "import",
                     "src/Service/Cart.php": "import",
                     "src/Service/Pay.php": "import"}
    # `use function` is ignored; nothing resolves -> no edges
    assert php_edges("src/Other.php", sources["src/Other.php"], idx) == []


def test_php_trait_use_inside_class_resolves_same_namespace():
    from megabrain.graph import php_class_index, php_edges
    sources = {
        "src/Svc.php": ("<?php\nnamespace App;\nclass Svc {\n    use LogsActivity;\n}\n"),
        "src/LogsActivity.php": "<?php\nnamespace App;\ntrait LogsActivity {}\n",
    }
    idx = php_class_index(sources)
    edges = php_edges("src/Svc.php", sources["src/Svc.php"], idx)
    assert edges == [("src/LogsActivity.php", "import")]


def test_php_strategy_wires_the_graph():
    from megabrain.strategies import PhpStrategy
    s = PhpStrategy("demo")
    sources = {
        "a.php": "<?php\nnamespace X;\nuse X\\B;\nclass A {}\n",
        "b.php": "<?php\nnamespace X;\nclass B {}\n",
    }
    ctx = s.build_edge_ctx(sources, "demo")
    assert s.extract_edges("a.php", sources["a.php"], ctx) == [("b.php", "import")]


def test_mixed_html_template_partition_is_clean():
    """Regression: PHP mixed-HTML `text` nodes swallow the trailing newline and
    reported an end line one past the file — breaking the partition."""
    tpl = ('<?php use App\\Support\\LogsActivity; ?>\n'
           '<html>\n<body>\n'
           '<?php foreach ($items as $i): ?>\n'
           '  <p><?php echo $i; ?></p>\n'
           '<?php endforeach; ?>\n'
           '</body>\n</html>\n')
    r = TreeSitterChunker(PHP_SPEC).chunk_file("templates/x.php", tpl)
    assert not validate_partition(r)
    assert r.chunks[-1].end_line == len(tpl.splitlines())


def test_full_index_keeps_edges_regardless_of_file_order(tmp_path, fake_embedder):
    """End-to-end: an importer that sorts BEFORE its target keeps its edge."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "AController.php").write_text(
        "<?php\nnamespace App;\nuse App\\ZRepo;\nclass AController {}\n")
    (tmp_path / "src" / "ZRepo.php").write_text(
        "<?php\nnamespace App;\nclass ZRepo {}\n")
    from megabrain.indexer import index_repo
    from megabrain.store import Store
    index_repo(tmp_path, quiet=True)
    rows = Store(tmp_path).db.execute("SELECT src,dst FROM edges").fetchall()
    assert ("src/AController.php", "src/ZRepo.php") in rows
