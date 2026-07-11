"""forge --specialize: opportunity detection, the changed-files A/B gate, and
the install-only-on-measured-win rule. Offline: canned LLM over
providers.chat_text + conftest's FakeEmbedder (token-hash → deterministic)."""

import pytest

from megabrain.forge_eval import ab_gate, changed_files, probe_spans
from megabrain.forge_specialize import detect_specialization, specialize
from megabrain.indexing import strategies as strat_mod

# A data-table module: >120 lines, one dict dominating the file. Entry values
# carry distinctive tokens so probe queries separate cleanly even with
# token-hash embeddings.
_ENTRIES = "\n".join(
    f'    {100 + i}: (\n        "{w}_alpha",\n        "{w}_beta",\n        "{w}_gamma",\n    ),'
    for i, w in enumerate(
        "aardvark bison caiman dugong echidna fossa gharial hoatzin ibex jerboa "
        "kinkajou lemur markhor numbat okapi pangolin quokka rhea serval tapir "
        "urial vicuna wombat xerus yapok zorilla axolotl bandicoot capybara dhole".split()))
TABLE_PY = f'"""Big lookup table."""\n\n_codes = {{\n{_ENTRIES}\n}}\n'
NORMAL_PY = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"


def _mk_strategy(group: int):
    """Reference shape-router: table files split per `group` entries; everything
    else delegates to the built-in byte-identically."""
    from megabrain import Chunk, FileResult
    from megabrain.chunkers import nws  # noqa: F401 (mirrors generated imports)
    from megabrain.indexing.strategies import builtin_strategy_for

    class PySpecialStrategy:
        exts = (".py",)

        def __init__(self, repo: str = ""):
            self.repo = repo
            self._fallback = builtin_strategy_for(".py", repo)

        def chunk_file(self, relpath, source):
            if "_codes = {" not in source:
                return self._fallback.chunk_file(relpath, source)
            lines = source.splitlines(keepends=True)
            total = len(lines)
            first = next(i for i, ln in enumerate(lines, 1) if "_codes = {" in ln)
            cuts = [1] + list(range(first + 1, total, group))
            bounds = list(zip(cuts, [c - 1 for c in cuts[1:]] + [total]))
            chunks = [Chunk(relpath, "block", f"L{s}-{e}", s, e,
                            "".join(lines[s - 1:e]), f"{relpath} > L{s}-{e}").finalize()
                      for s, e in bounds]
            return FileResult(relpath, chunks, [], f"# {relpath}", True, total)

        def build_edge_ctx(self, sources, repo_name):
            return None

        def extract_edges(self, relpath, source, ctx):
            return None

    return PySpecialStrategy


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "table.py").write_text(TABLE_PY)
    (tmp_path / "normal.py").write_text(NORMAL_PY)
    (tmp_path / "other.py").write_text(NORMAL_PY.replace("add", "mul"))
    monkeypatch.setattr(strat_mod, "TRUST_STORE", tmp_path / "trust.json")
    return tmp_path


def test_detect_finds_the_data_table(repo):
    opps = detect_specialization(repo)
    assert [o["ext"] for o in opps] == [".py"]
    assert opps[0]["target"] == "table.py"
    assert "dict/list literal" in opps[0]["diagnoses"]["table.py"]


def test_probe_spans_are_the_dict_entries(repo):
    probes = probe_spans(repo / "table.py")
    assert len(probes) == 30
    assert all(a <= b for _, a, b in probes)
    assert "aardvark" in probes[0][0]


def test_changed_files_is_exactly_the_special_file(repo):
    strat = _mk_strategy(group=3)(repo="r")
    assert changed_files(repo, strat) == ["table.py"]


def test_gate_rejects_a_noop_candidate(repo, fake_embedder):
    from megabrain.indexing.strategies import builtin_strategy_for

    class Noop:
        exts = (".py",)

        def __init__(self):
            self._fb = builtin_strategy_for(".py", "r")

        def chunk_file(self, relpath, source):
            return self._fb.chunk_file(relpath, source)

        def build_edge_ctx(self, s, n):
            return None

        def extract_edges(self, r, s, c):
            return None

    res = ab_gate(repo, Noop())
    assert not res["win"] and res["reason"] == "candidate changes no files"


def test_gate_accepts_tight_and_rejects_coarse(repo, fake_embedder):
    tight = ab_gate(repo, _mk_strategy(group=3)(repo="r"))
    assert tight["win"] and tight["delta_iou"] > 0
    assert tight["changed_files"] == ["table.py"]
    # a barely-different candidate (2 giant chunks) must not clear the margin
    coarse = ab_gate(repo, _mk_strategy(group=1000)(repo="r"))
    assert not coarse["win"]
    assert coarse["delta_iou"] < tight["delta_iou"]


def _code_for(group: int) -> str:
    """The reference strategy as installable source (what the fake LLM emits)."""
    return f'''
from megabrain import Chunk, FileResult
from megabrain.indexing.strategies import builtin_strategy_for


class PySpecialStrategy:
    exts = (".py",)

    def __init__(self, repo: str = ""):
        self.repo = repo
        self._fallback = builtin_strategy_for(".py", repo)

    def chunk_file(self, relpath, source):
        if "_codes = {{" not in source:
            return self._fallback.chunk_file(relpath, source)
        lines = source.splitlines(keepends=True)
        total = len(lines)
        first = next(i for i, ln in enumerate(lines, 1) if "_codes = {{" in ln)
        cuts = [1] + list(range(first + 1, total, {group}))
        bounds = list(zip(cuts, [c - 1 for c in cuts[1:]] + [total]))
        chunks = [Chunk(relpath, "block", f"L{{s}}-{{e}}", s, e,
                        "".join(lines[s - 1:e]), f"{{relpath}} > L{{s}}-{{e}}").finalize()
                  for s, e in bounds]
        return FileResult(relpath, chunks, [], f"# {{relpath}}", True, total)

    def build_edge_ctx(self, sources, repo_name):
        return None

    def extract_edges(self, relpath, source, ctx):
        return None
'''


def test_specialize_installs_only_on_win(repo, monkeypatch, fake_embedder):
    monkeypatch.setattr("megabrain.providers.chat_text",
                        lambda *a, **k: f"```python\n{_code_for(3)}```")
    rep = specialize(repo, ext=".py", quiet=True)
    e = rep["specialized"][0]
    assert e["ok"] and e["gate"]["win"]
    assert (repo / ".megabrain/strategies/py.py").exists()
    # installed → trusted → auto-loaded
    assert [s for s in strat_mod.load_repo_strategies(repo, "r") if ".py" in s.exts]


def test_specialize_rejects_and_does_not_install_a_coarse_one(repo, monkeypatch,
                                                              fake_embedder):
    monkeypatch.setattr("megabrain.providers.chat_text",
                        lambda *a, **k: f"```python\n{_code_for(1000)}```")
    rep = specialize(repo, ext=".py", quiet=True)
    e = rep["specialized"][0]
    assert e["ok"] and not e["gate"]["win"]
    assert not (repo / ".megabrain/strategies/py.py").exists()
    assert "index" not in rep
