"""Legacy-PHP section chunker — routing, partition guarantees, doc attachment,
banner sections, and the chunks_for_file per-chunk score/selected API."""

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_php")

from megabrain.chunker import validate_partition                    # noqa: E402
from megabrain.chunker_php import (LegacyPhpChunker, PhpChunker,    # noqa: E402
                                   looks_legacy)
from megabrain.chunker_ts import PHP_SPEC, TreeSitterChunker, _parser  # noqa: E402

LEGACY = """<?php
//---------------------------------------------------------
// INCLUDES
//---------------------------------------------------------
require_once('inc/config.php');
$db = conectar_db();
$usuario = $_SESSION['usuario'];

//---------------------------------------------------------
// calcularTotal()
//
// calculo del total con IVA y descuento
//---------------------------------------------------------
function calcularTotal($items, $desc) {
    $total = 0;
    foreach ($items as $it) {
        $total += $it['precio'] * $it['cant'];
    }
    $total = $total * (1 - $desc);
    return $total * 1.21;
}

function limpiar($s) { return trim(strip_tags($s)); }
?>
<html>
<body bgcolor="#FFFFFF">
<table border="1"><tr><td>Sistema de Gestion</td></tr></table>
<?php if ($usuario == '') { ?>
  <p>Acceso denegado</p>
<?php } ?>
</body>
</html>
"""

MODERN = ("<?php\nnamespace App;\n\nclass Foo {\n"
          "    public function bar() { return 1; }\n"
          "    public function baz() { return 2; }\n}\n")


def _root(src: str):
    return _parser(PHP_SPEC, "php").parse(src.encode()).root_node


def test_detection_legacy_vs_modern():
    assert looks_legacy(_root(LEGACY))
    assert not looks_legacy(_root(MODERN))          # namespace -> always modern


def test_modern_routing_is_byte_identical():
    a = PhpChunker(repo="d").chunk_file("src/Foo.php", MODERN)
    b = TreeSitterChunker(PHP_SPEC, repo="d").chunk_file("src/Foo.php", MODERN)
    assert [(c.kind, c.name, c.start_line, c.end_line) for c in a.chunks] == \
           [(c.kind, c.name, c.start_line, c.end_line) for c in b.chunks]
    assert a.skeleton == b.skeleton


def test_legacy_partition_is_clean():
    r = PhpChunker(repo="d").chunk_file("main.php", LEGACY)
    assert r.parse_ok
    assert not validate_partition(r)
    assert r.chunks[-1].end_line == len(LEGACY.splitlines())


def test_functions_stand_alone_with_doc_attached():
    r = PhpChunker(repo="d").chunk_file("main.php", LEGACY)
    by_name = {c.name: c for c in r.chunks}
    fn = by_name["calcularTotal"]
    assert fn.kind == "function"
    # the banner doc header above the def travels WITH the function chunk
    assert "calculo del total con IVA y descuento" in fn.text
    assert "function calcularTotal" in fn.text
    # a second tiny function is still its own chunk (defs never merge)
    assert by_name["limpiar"].kind == "function"


def test_fat_doc_banner_still_attaches_to_its_function():
    """Regression: a doc header heavier than BANNER_MIN_FLUSH must not be cut
    off by its own CLOSING banner — it belongs to the function below it."""
    doc = ("//---------------------------------------------------------\n"
           "// procesarLiquidacion()\n"
           + "".join(f"// linea de documentacion numero {i} con bastante texto explicativo\n"
                     for i in range(12))
           + "//---------------------------------------------------------\n")
    src = ("<?php\n"
           "require_once('inc/config.php');\n"
           "$x = 1; $y = 2; $z = 3;\n\n"
           + doc +
           "function procesarLiquidacion($items) {\n"
           "    return count($items);\n"
           "}\n")
    r = PhpChunker(repo="d").chunk_file("liq.php", src)
    assert not validate_partition(r)
    fn = next(c for c in r.chunks if c.name == "procesarLiquidacion")
    assert "linea de documentacion numero 3" in fn.text   # doc attached
    assert "function procesarLiquidacion" in fn.text


def test_banner_names_the_section_and_becomes_heading_symbol():
    r = PhpChunker(repo="d").chunk_file("main.php", LEGACY)
    named = [c for c in r.chunks if c.kind == "module" and c.name]
    assert any(c.name == "INCLUDES" for c in named)
    heads = [(s.name, s.kind) for s in r.symbols if s.kind == "heading"]
    assert ("INCLUDES", "heading") in heads
    assert "// INCLUDES" in r.skeleton


def test_html_tail_gets_html_kind():
    r = PhpChunker(repo="d").chunk_file("main.php", LEGACY)
    tail = r.chunks[-1]
    assert tail.kind == "html"
    assert "<html>" in tail.text


def test_broken_legacy_does_not_crash():
    r = PhpChunker().chunk_file("x.php", "<?php function {{{ nope\n$a = 1;\n")
    assert r.chunks
    assert not validate_partition(r)


def test_r2app_main_php_real_file():
    """The chunker's origin story: a real 2400-line year-2000 PHP page."""
    p = Path.home() / "servicehub/dev/r2-app/main.php"
    if not p.exists():
        pytest.skip("r2-app not on this machine")
    src = p.read_text(errors="replace")
    root = _root(src)
    assert looks_legacy(root)
    r = LegacyPhpChunker(repo="r2").chunk_file("main.php", src, root=root)
    assert not validate_partition(r)
    assert len(r.chunks) > 5                       # real sections, not one blob
    assert any(s.kind == "heading" for s in r.symbols)


def test_demo_app_every_file_partitions():
    d = Path.home() / "megabrain-chunk-demo/legacy-php-app"
    if not d.exists():
        pytest.skip("demo app not on this machine")
    ch = PhpChunker(repo="demo")
    for p in sorted(d.rglob("*.php")):
        r = ch.chunk_file(p.relative_to(d).as_posix(), p.read_text(errors="replace"))
        assert not validate_partition(r), p.name


# ---------------------------------------------------------------- chunks API

def test_chunks_for_file_scores_and_selection(tmp_path, fake_embedder):
    (tmp_path / "inc").mkdir()
    (tmp_path / "inc" / "funciones.php").write_text(LEGACY)
    (tmp_path / "otros.php").write_text(
        "<?php\nfunction sumar($a, $b) { return $a + $b; }\n")
    from megabrain.indexer import index_repo
    index_repo(tmp_path, quiet=True)

    from megabrain.query import chunks_for_file_root, load_state, search_with_state
    res = chunks_for_file_root(tmp_path, "inc/funciones.php",
                               "calcular total factura IVA descuento")
    assert res["chunks"], "file must have chunks"
    assert len(res["chunks"]) >= 3                 # legacy chunker granularity
    # every chunk carries a float score; at least one is selected
    assert all(isinstance(c["score"], float) for c in res["chunks"])
    assert res["selected_count"] >= 1
    # the function chunk about the total wins over the html tail
    best = max(res["chunks"], key=lambda c: c["score"])
    assert best["name"] == "calcularTotal"
    assert best["selected"]
    # scored= reuse gives the same result as a fresh search (single-scoring path)
    st = load_state(tmp_path)
    full = search_with_state(st, "calcular total factura IVA descuento")
    t1 = next(t for t in full["tier1"] if t["file"] == "inc/funciones.php")
    api_scores = {c["id"]: c["score"] for c in res["chunks"]}
    for c in t1["chunks"]:
        assert api_scores[c["id"]] == pytest.approx(c["score"])
