"""Concurrent-embedding unit tests: N parallel /embeddings requests must be
indistinguishable from the sequential path — same rows, same order, same cache,
same accounting, same failure semantics. All offline (post_json monkeypatched).
"""

import threading
import time

import numpy as np
import pytest

import megabrain.providers.embeddings as E
from megabrain import providers
from megabrain.errors import ProviderError


@pytest.fixture
def embedder(monkeypatch, tmp_path):
    e = E.Embedder(api_key="test-key", cache_dir=tmp_path / "cache")
    e.workers = 4
    return e


def _vec_for(text: str) -> list[float]:
    """A distinct, recognizable unit vector per input text."""
    return [float(len(text)), 1.0]


def _api(calls=None, delay=0.0, fail_on=None):
    """Fake post_json: echoes one vector per input, derived from the text —
    so a row landing in the wrong slot is detectable, not coincidental."""
    def fake(path, body, key=None, retries=5, timeout=120, base_url=None):
        if fail_on is not None and any(fail_on in t for t in body["input"]):
            raise ProviderError("boom")
        if delay:
            time.sleep(delay)
        if calls is not None:
            calls.append(list(body["input"]))
        return {"data": [{"index": i, "embedding": _vec_for(t)}
                         for i, t in enumerate(body["input"])],
                "usage": {"total_tokens": 3, "cost": {"total_cost": 0.001}}}
    return fake


def test_concurrent_rows_land_by_input_index(embedder, monkeypatch):
    calls = []
    monkeypatch.setattr(providers, "post_json", _api(calls))
    texts = ["a" * (i + 1) for i in range(8)]           # 8 distinct lengths
    out = embedder.embed(texts, batch_size=1)           # 8 batches, 4 workers
    assert len(calls) == 8
    for i, t in enumerate(texts):
        expect = np.array(_vec_for(t), dtype=np.float32)
        assert np.allclose(out[i], expect / np.linalg.norm(expect)), \
            f"row {i} does not match its text under concurrency"


def test_serial_and_concurrent_results_are_identical(monkeypatch, tmp_path):
    monkeypatch.setattr(providers, "post_json", _api())
    texts = ["x" * (i + 1) for i in range(6)]
    seq = E.Embedder(api_key="k", cache_dir=tmp_path / "c1")
    seq.workers = 1
    par = E.Embedder(api_key="k", cache_dir=tmp_path / "c2")
    par.workers = 4
    assert np.allclose(seq.embed(texts, batch_size=2),
                       par.embed(texts, batch_size=2))


def test_usage_accounting_is_thread_safe(embedder, monkeypatch):
    calls = []
    monkeypatch.setattr(providers, "post_json", _api(calls, delay=0.005))
    embedder.embed(["t" * (i + 1) for i in range(12)], batch_size=1)
    assert embedder.tokens == 3 * len(calls) == 36
    assert abs(embedder.cost - 0.001 * 12) < 1e-9


def test_any_failed_batch_aborts_the_whole_call(embedder, monkeypatch):
    monkeypatch.setattr(providers, "post_json", _api(fail_on="bad"))
    with pytest.raises(ProviderError, match="boom"):
        embedder.embed(["ok1", "bad!", "ok2", "ok3"], batch_size=1)


def test_cache_hits_never_reach_the_network(embedder, monkeypatch):
    calls = []
    monkeypatch.setattr(providers, "post_json", _api(calls))
    embedder.embed(["one", "two2", "three"], batch_size=1)
    n = len(calls)
    again = embedder.embed(["one", "two2", "three"], batch_size=1)
    assert len(calls) == n, "cached texts re-requested"
    assert again.shape == (3, 2)


def test_cache_writes_survive_concurrency(embedder, monkeypatch):
    """Every vector must be on disk after a concurrent run (atomic tmp+replace
    per thread), so the next index run is a full cache hit."""
    monkeypatch.setattr(providers, "post_json", _api())
    texts = [f"txt{i}" for i in range(10)]
    embedder.embed(texts, batch_size=1)
    for t in texts:
        assert embedder._cpath(t).exists()


def test_a_refused_cache_write_does_not_fail_the_embed(embedder, monkeypatch):
    """Windows refuses os.replace while another thread holds the destination
    open (two threads racing the SAME cache entry — duplicate texts in one run).
    A lost cache write must never fail an index, and the vectors still return.
    Simulated here so the guard is covered on every platform, not only in CI's
    Windows job — where it surfaced as `PermissionError(13, Access is denied)`.
    """
    monkeypatch.setattr(providers, "post_json", _api())

    def refuse(self, target):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(E.Path, "replace", refuse)
    out = embedder.embed(["alpha", "beta12"], batch_size=1)
    assert out.shape == (2, 2)
    expect = np.array(_vec_for("alpha"), dtype=np.float32)
    assert np.allclose(out[0], expect / np.linalg.norm(expect))


def test_a_refused_cache_write_leaves_no_temp_files(embedder, monkeypatch):
    monkeypatch.setattr(providers, "post_json", _api())

    def refuse(self, target):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(E.Path, "replace", refuse)
    embedder.embed(["alpha", "beta12"], batch_size=1)
    assert list(embedder.cache.glob("*.tmp.npy")) == [], "temp files left behind"


def test_duplicate_texts_in_one_call_share_a_cache_entry(embedder, monkeypatch):
    """The same text twice in one embed() maps to ONE cache path — the race the
    Windows failure exposed. Both rows must still come back correct."""
    monkeypatch.setattr(providers, "post_json", _api())
    out = embedder.embed(["same one"] * 4, batch_size=1)
    assert out.shape == (4, 2)
    assert all(np.allclose(out[0], row) for row in out)
    assert embedder._cpath("same one").exists()


def test_on_batch_reports_monotonic_progress(embedder, monkeypatch):
    monkeypatch.setattr(providers, "post_json", _api(delay=0.002))
    seen = []
    embedder.embed(["p" * (i + 1) for i in range(6)], batch_size=1,
                   on_batch=lambda d, t: seen.append((d, t)))
    assert [d for d, _ in seen] == [1, 2, 3, 4, 5, 6]
    assert all(t == 6 for _, t in seen)


def test_concurrent_embed_calls_on_same_text_do_not_collide(monkeypatch, tmp_path):
    """Two embed() calls in one process racing on the SAME text (server-mode
    reality) must both finish with a valid cache file — the tmp name carries
    the thread id precisely for this."""
    monkeypatch.setattr(providers, "post_json", _api(delay=0.005))
    e = E.Embedder(api_key="k", cache_dir=tmp_path / "c")
    e.workers = 2
    errs = []

    def run():
        try:
            e.embed(["shared text"] * 1)
        except Exception as ex:  # noqa: BLE001
            errs.append(ex)
    ts = [threading.Thread(target=run) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    assert np.load(e._cpath("shared text")).shape == (2,)


# ---------------------------------------------------------- worker resolution

def test_env_pins_concurrency(monkeypatch, tmp_path):
    monkeypatch.setenv("MEGABRAIN_EMBED_CONCURRENCY", "3")
    e = E.Embedder(api_key="k", cache_dir=tmp_path / "c")
    assert e.workers == 3


def test_local_endpoint_defaults_to_serial(monkeypatch, tmp_path):
    monkeypatch.delenv("MEGABRAIN_EMBED_CONCURRENCY", raising=False)
    monkeypatch.setattr(providers, "EMBED_BASE_URL", "http://localhost:11434/v1")
    e = E.Embedder(api_key="k", cache_dir=tmp_path / "c")
    assert e.workers == 1


def test_remote_endpoint_defaults_to_parallel(monkeypatch, tmp_path):
    monkeypatch.delenv("MEGABRAIN_EMBED_CONCURRENCY", raising=False)
    monkeypatch.setattr(providers, "EMBED_BASE_URL", "https://openrouter.ai/api/v1")
    e = E.Embedder(api_key="k", cache_dir=tmp_path / "c")
    assert e.workers == 8
