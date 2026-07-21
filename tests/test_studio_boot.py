"""How `studio`/`serve-api` pick the boot repo, and what the banner claims.

Two real confusions this pins down: running studio one directory INSIDE an
indexed repo used to fall through to "newest registry entry" (it looked like
studio chose a repo at random), and the banner named that single repo while
the UI rail listed every registry repo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import megabrain.server.http as http_mod
from megabrain.indexing.indexer import index_repo


class _StubServer:
    """Stands in for ThreadingHTTPServer so serve() runs to the banner and
    returns instead of blocking in serve_forever()."""

    def __init__(self, addr, handler):
        self.server_address = addr
        self.handler = handler

    def serve_forever(self):
        return None

    def shutdown(self):
        return None


@pytest.fixture
def stub_server(monkeypatch):
    monkeypatch.setattr(http_mod, "ThreadingHTTPServer", _StubServer)


def _banner(capsys) -> str:
    """The `megabrain studio → …` line from whatever serve() just printed."""
    lines = [ln for ln in capsys.readouterr().out.splitlines()
             if ln.startswith("megabrain ")]
    assert lines, "serve() printed no banner"
    return lines[-1]


def _second_repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("other")
    (root / "svc.py").write_text(
        'def serve_it():\n    """Another repo entirely."""\n    return 7\n')
    index_repo(root)
    return root


# ------------------------------------------------------- boot repo resolution

def test_studio_from_a_subdir_serves_the_ancestor_repo(tiny_repo, stub_server,
                                                       capsys):
    """The reported confusion: launched from `<repo>/auth`, studio must serve
    <repo> — not fall through to whatever the registry happened to list first."""
    http_mod.serve(tiny_repo / "auth", port=0, serve_ui=True)
    assert f"repo={tiny_repo.name}" in _banner(capsys)


def test_serve_api_also_resolves_the_ancestor(tiny_repo, stub_server, capsys):
    """Headless serve-api resolves the same way — one rule, both surfaces."""
    http_mod.serve(tiny_repo / "billing", port=0, serve_ui=False)
    assert f"repo={tiny_repo.name}" in _banner(capsys)


def test_an_indexed_cwd_still_wins_over_the_registry(tiny_repo, stub_server,
                                                     capsys, tmp_path_factory):
    """An indexed cwd outranks the registry — even when another repo was
    indexed more recently and therefore heads the newest-first list."""
    from megabrain.storage.registry import list_repos
    other = _second_repo(tmp_path_factory)
    assert list_repos()[0]["path"] == str(other), "fixture: other must be newest"
    http_mod.serve(tiny_repo, port=0, serve_ui=True)
    assert f"default={tiny_repo.name}" in _banner(capsys)


def test_unindexed_cwd_falls_back_to_the_newest_registry_repo(
        tiny_repo, stub_server, capsys, tmp_path_factory):
    other = _second_repo(tmp_path_factory)      # indexed after tiny_repo
    elsewhere = tmp_path_factory.mktemp("not_a_repo")
    http_mod.serve(elsewhere, port=0, serve_ui=True)
    assert f"default={other.name}" in _banner(capsys)


# -------------------------------------------------------------------- banner

def test_banner_reports_the_count_and_the_default(tiny_repo, stub_server,
                                                  capsys, tmp_path_factory):
    """With several repos loaded the banner must say how many AND which one
    answers a request that omits ?repo= — naming one alone read as 'the only
    repo loaded' while the rail showed ten."""
    _second_repo(tmp_path_factory)
    http_mod.serve(tiny_repo, port=0, serve_ui=True)
    banner = _banner(capsys)
    assert "repos=2 (registry)" in banner
    assert f"default={tiny_repo.name}" in banner
    assert " repo=" not in banner, "singular form used while serving many"


def test_single_repo_banner_stays_singular(tiny_repo, stub_server, capsys):
    http_mod.serve(tiny_repo, port=0, serve_ui=True)
    banner = _banner(capsys)
    assert f"repo={tiny_repo.name}" in banner
    assert "default=" not in banner and "repos=" not in banner


def test_empty_registry_and_unindexed_cwd_says_none_loaded(
        stub_server, capsys, tmp_path_factory, monkeypatch):
    monkeypatch.setenv("MEGABRAIN_REGISTRY",
                       str(tmp_path_factory.mktemp("empty") / "registry.json"))
    http_mod.serve(tmp_path_factory.mktemp("bare"), port=0, serve_ui=True)
    assert "none loaded" in _banner(capsys)


def test_banner_chunk_count_belongs_to_the_default_repo(tiny_repo, stub_server,
                                                        capsys):
    from megabrain.storage.store import Store
    with Store(tiny_repo) as s:
        expected = s.stats()["chunks"]
    http_mod.serve(tiny_repo, port=0, serve_ui=True)
    assert f"chunks={expected}" in _banner(capsys)
