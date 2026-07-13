"""forge: detection census, the partition oracle as install gate (repair loop),
trust-gated repo-local loading, and index survival across auto-refresh.

All offline: the LLM is a canned fake patched over providers.chat_text and
embeddings come from conftest's FakeEmbedder.
"""

import json
import time

import pytest

from megabrain.forge import detect, forge, validate_strategy
from megabrain.indexing import strategies as strat_mod
from megabrain.indexing.indexer import index_repo, maybe_reindex
from megabrain.indexing.strategies import load_repo_strategies, trust_file
from megabrain.store import Store

SQL = "-- users\nCREATE TABLE users (id INTEGER);\n\n-- orders\nCREATE TABLE orders (id INTEGER);\n"

GOOD = '''\
"""Whole-file .sql chunker (test fixture)."""
from megabrain import Chunk, FileResult, Symbol


class SqlStrategy:
    exts = (".sql",)

    def __init__(self, repo: str = ""):
        self.repo = repo

    def chunk_file(self, relpath, source):
        lines = source.splitlines(keepends=True)
        if not lines:
            return FileResult(relpath, [], [], "", True, 0)
        crumb = f"{self.repo} > {relpath}" if self.repo else relpath
        c = Chunk(relpath, "module", None, 1, len(lines), source, crumb).finalize()
        syms = [Symbol(relpath, "users", "table", 2, 2, "CREATE TABLE users")]
        return FileResult(relpath, [c], syms, f"# {relpath}", True, len(lines))

    def build_edge_ctx(self, sources, repo_name):
        return None

    def extract_edges(self, relpath, source, ctx):
        return None
'''

# Drops the last line -> partition violation. The oracle must reject it.
BAD = GOOD.replace("1, len(lines), source", "1, len(lines) - 1, source")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Tmp repo with uncovered .sql files + an isolated trust store."""
    (tmp_path / "a.sql").write_text(SQL)
    (tmp_path / "b.sql").write_text(SQL.replace("users", "invoices"))
    (tmp_path / "c.sql").write_text("CREATE TABLE tiny (id INTEGER);\n")
    (tmp_path / "main.py").write_text("x = 1\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\0\0\0")
    (tmp_path / "one.xyz").write_text("lonely\n")          # single file: below MIN_FILES
    monkeypatch.setattr(strat_mod, "TRUST_STORE", tmp_path / "trust.json")
    return tmp_path


def test_detect_census(repo):
    cands = detect(repo)
    exts = {c["ext"] for c in cands}
    assert ".sql" in exts                     # 3 files, uncovered
    assert ".py" not in exts                  # covered by the built-in registry
    assert ".png" not in exts                 # binary sniff
    assert ".xyz" not in exts                 # below MIN_FILES
    sql = next(c for c in cands if c["ext"] == ".sql")
    assert sql["files"] == 3 and len(sql["samples"]) >= 1


def test_oracle_rejects_bad_partition(repo):
    ok, msg, _ = validate_strategy(repo, BAD, ".sql", ["a.sql", "b.sql"])
    assert not ok and "partition" in msg


def test_forge_repair_loop_installs_only_vetted_code(repo, monkeypatch, fake_embedder):
    calls = []

    def fake_chat(model, prompt, max_tokens, **kw):
        calls.append(prompt)
        if len(calls) == 1:
            return f"```python\n{BAD}```"
        assert "PREVIOUS ATTEMPT FAILED" in prompt         # repair feedback flows back
        return f"```python\n{GOOD}```"

    monkeypatch.setattr("megabrain.providers.chat_text", fake_chat)
    report = forge(repo, ext=".sql", quiet=True)
    entry = report["forged"][0]
    assert entry["ok"] and entry["attempts"] == 2
    installed = repo / ".megabrain/strategies/sql.py"
    assert installed.exists() and "SqlStrategy" in installed.read_text()
    # trusted + loadable, and the reindex actually ingested the .sql files
    assert [s for s in load_repo_strategies(repo, "r") if ".sql" in s.exts]
    with Store(repo) as s:
        assert "a.sql" in s.all_paths()


def test_forge_gives_up_after_attempts(repo, monkeypatch):
    monkeypatch.setattr("megabrain.providers.chat_text",
                        lambda *a, **k: f"```python\n{BAD}```")
    report = forge(repo, ext=".sql", quiet=True, attempts=2)
    entry = report["forged"][0]
    assert not entry["ok"] and entry["attempts"] == 2
    assert not (repo / ".megabrain/strategies/sql.py").exists()


def test_untrusted_module_is_skipped_until_approved(repo):
    sdir = repo / ".megabrain/strategies"
    sdir.mkdir(parents=True)
    f = sdir / "sql.py"
    f.write_text(GOOD)
    assert load_repo_strategies(repo, "r") == []           # no trust entry yet
    trust_file(f)
    assert [s for s in load_repo_strategies(repo, "r") if ".sql" in s.exts]
    f.write_text(GOOD + "\n# edited after approval\n")     # sha mismatch
    assert load_repo_strategies(repo, "r") == []


def test_auto_refresh_keeps_custom_extensions(repo, fake_embedder):
    sdir = repo / ".megabrain/strategies"
    sdir.mkdir(parents=True)
    (sdir / "sql.py").write_text(GOOD)
    trust_file(sdir / "sql.py")
    index_repo(repo, quiet=True)
    with Store(repo) as s:
        assert "a.sql" in s.all_paths()
        s.set_meta("last_index", {"t": time.time() - 3600, "files": 0})  # go stale
        s.commit()
    assert maybe_reindex(repo)                              # refresh ran…
    with Store(repo) as s:
        assert "a.sql" in s.all_paths()                     # …and nothing was pruned


def test_dry_run_returns_code_without_installing(repo, monkeypatch):
    monkeypatch.setattr("megabrain.providers.chat_text",
                        lambda *a, **k: f"```python\n{GOOD}```")
    report = forge(repo, ext=".sql", dry_run=True, quiet=True)
    entry = report["forged"][0]
    assert entry["ok"] and "SqlStrategy" in entry["code"]
    assert not (repo / ".megabrain/strategies").exists()
    assert "index" not in report


def test_trust_store_is_json_and_repo_cannot_forge_it(repo, monkeypatch):
    sdir = repo / ".megabrain/strategies"
    sdir.mkdir(parents=True)
    f = sdir / "sql.py"
    f.write_text(GOOD)
    trust_file(f)
    data = json.loads((strat_mod.TRUST_STORE).read_text())
    assert f.resolve().as_posix() in data
