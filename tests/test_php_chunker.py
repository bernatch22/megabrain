"""PHP chunker (tree-sitter LangSpec) — skipped when the grammar isn't installed."""

import pytest

pytest.importorskip("tree_sitter_php")

from megabrain.chunker import validate_partition          # noqa: E402
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
