"""Two-phase indexing tests: chunk everything first, then ONE embed call over
all texts (chunks + skeletons), then write. The old per-file embed cost 2+
HTTP round trips per changed file — ~20 min cold on a rails-sized repo.

These tests pin the contract of the restructure: one embed call per index run,
per-file vectors identical to the per-file era, incremental sha-skip intact,
and an embed failure leaving the store byte-untouched.
"""

from __future__ import annotations

import numpy as np
import pytest

import megabrain.indexing.indexer as indexer
from megabrain.storage.store import Store
from tests.conftest import FakeEmbedder


class RecordingEmbedder(FakeEmbedder):
    """FakeEmbedder that records every embed() call's texts."""

    calls: list[list[str]] = []      # rebound per test via `fresh`

    def embed(self, texts, batch_size=64, on_batch=None):
        type(self).calls.append(list(texts))
        return super().embed(texts, batch_size=batch_size, on_batch=on_batch)


@pytest.fixture
def recording(monkeypatch):
    RecordingEmbedder.calls = []
    monkeypatch.setattr(indexer, "Embedder", RecordingEmbedder)
    import megabrain.retrieval.state as state
    monkeypatch.setattr(state, "Embedder", RecordingEmbedder)
    return RecordingEmbedder


def _repo(tmp_path):
    (tmp_path / "a.py").write_text(
        'def alpha():\n    """First helper."""\n    return 1\n')
    (tmp_path / "b.py").write_text(
        'def beta():\n    """Second helper."""\n    return 2\n')
    (tmp_path / "c.py").write_text(
        'def gamma():\n    """Third helper."""\n    return 3\n')
    return tmp_path


def test_cold_index_makes_exactly_one_embed_call(tmp_path, recording):
    indexer.index_repo(_repo(tmp_path))
    assert len(recording.calls) == 1, \
        f"expected ONE global embed call, got {len(recording.calls)}"
    joined = "\n".join(recording.calls[0])
    for name in ("alpha", "beta", "gamma"):
        assert name in joined, f"{name}'s chunks missing from the global batch"


def test_skeletons_ride_in_the_same_call(tmp_path, recording):
    indexer.index_repo(_repo(tmp_path))
    with Store(tmp_path) as s:
        paths, _, m = s.load_file_matrix()
    assert sorted(paths) == ["a.py", "b.py", "c.py"], "a file lost its skel_vec"
    assert m.shape[0] == 3
    assert len(recording.calls) == 1, "skeletons went out as separate requests"


def test_every_chunk_row_has_a_vector(tmp_path, recording):
    indexer.index_repo(_repo(tmp_path))
    with Store(tmp_path) as s:
        metas, m = s.load_matrix()
        total = s.stats()["chunks"]
    assert total == len(metas) == m.shape[0] > 0, "chunk rows without vectors"


def test_vectors_match_the_per_file_era(tmp_path, recording):
    """Global batching must not change WHAT gets embedded: each chunk's vector
    equals embedding that chunk's text alone (order/slicing is transparent)."""
    from megabrain.chunkers import embed_text
    indexer.index_repo(_repo(tmp_path))
    with Store(tmp_path) as s:
        metas, m = s.load_matrix()
        chunks = {c["id"]: c for p in s.all_paths() for c in s.file_chunks(p)}
    solo = FakeEmbedder()
    for meta, vec in zip(metas, m):
        c = chunks[meta.id]
        from megabrain.chunkers.base import Chunk
        text = embed_text(Chunk(file=meta.file, kind=c["kind"], name=c["name"],
                                part=c["part"], start_line=c["start_line"],
                                end_line=c["end_line"], text=c["text"],
                                breadcrumb=c["breadcrumb"]))
        assert np.allclose(vec, solo.embed([text])[0], atol=1e-6), \
            f"vector drifted for {meta.file}:{meta.start_line}"


def test_incremental_embeds_only_the_changed_file(tmp_path, recording):
    root = _repo(tmp_path)
    indexer.index_repo(root)
    recording.calls.clear()
    (root / "b.py").write_text(
        'def beta_v2():\n    """Changed helper."""\n    return 22\n')
    stats = indexer.index_repo(root)
    assert stats["changed"] == 1 and stats["unchanged"] == 2
    assert len(recording.calls) == 1
    joined = "\n".join(recording.calls[0])
    assert "beta_v2" in joined
    assert "alpha" not in joined and "gamma" not in joined, \
        "unchanged files re-embedded"


def test_noop_reindex_makes_no_embed_call(tmp_path, recording):
    root = _repo(tmp_path)
    indexer.index_repo(root)
    recording.calls.clear()
    stats = indexer.index_repo(root)
    assert stats["changed"] == 0
    assert recording.calls == [], "no-op reindex still called the embedder"


def test_embed_failure_leaves_the_store_untouched(tmp_path, recording, monkeypatch):
    """Phase order guarantees it: embedding happens BEFORE any delete/insert,
    so a provider failure mid-index can never leave a half-written pass."""
    root = _repo(tmp_path)
    indexer.index_repo(root)
    with Store(root) as s:
        before = (s.stats(), s.file_sha("b.py"))
    (root / "b.py").write_text(
        'def beta_v2():\n    """Changed helper."""\n    return 22\n')

    class Exploding(FakeEmbedder):
        def embed(self, texts, batch_size=64, on_batch=None):
            raise RuntimeError("provider down")
    monkeypatch.setattr(indexer, "Embedder", Exploding)
    with pytest.raises(RuntimeError, match="provider down"):
        indexer.index_repo(root)
    with Store(root) as s:
        after = (s.stats(), s.file_sha("b.py"))
    assert before == after, "failed index mutated the store"


def test_edges_and_symbols_still_written(tmp_path, recording):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "core.py").write_text(
        'def core_fn():\n    """Core."""\n    return 0\n')
    (tmp_path / "pkg" / "user.py").write_text(
        'from pkg.core import core_fn\n\n\n'
        'def use_it():\n    """Uses core."""\n    return core_fn()\n')
    indexer.index_repo(tmp_path, repo_name="pkg")
    with Store(tmp_path) as s:
        stats = s.stats()
        assert stats["symbols"] > 0
        assert ("pkg/user.py", "pkg/core.py") in {(a, b) for a, b, _ in s.all_edges()}


def test_progress_ticks_per_file_then_embed_events(tmp_path, recording):
    root = _repo(tmp_path)
    events = []
    indexer.index_repo(root, on_progress=events.append)
    files = [e for e in events if "file" in e]
    embeds = [e for e in events if e.get("type") == "embed"]
    assert len(files) == 3 and all(e["n"] == 3 for e in files)
    assert embeds and embeds[-1]["done"] == embeds[-1]["total"]
