"""embeddings.py unit tests: wire-format decode, L2 norm, disk cache, dims.
All offline — the HTTP layer is monkeypatched."""

import base64

import numpy as np
import pytest

import megabrain.providers.embeddings as E
from megabrain import providers


@pytest.fixture
def embedder(monkeypatch, tmp_path):
    # construction-time config: the cache dir and dims are injected, not
    # module globals (two embedders with different configs can coexist)
    e = E.Embedder(api_key="test-key", cache_dir=tmp_path / "cache")
    return e


def _api(vectors, calls=None):
    """Fake providers.post_json returning the given per-input embeddings."""
    def fake(path, body, key=None, retries=5, timeout=120, base_url=None):
        if calls is not None:
            calls.append(list(body["input"]))
        return {"data": [{"index": i, "embedding": v}
                         for i, v in enumerate(vectors[: len(body["input"])])],
                "usage": {"total_tokens": 3, "cost": {"total_cost": 0.001}}}
    return fake


def test_int8_base64_decoded_and_normalized(embedder, monkeypatch):
    raw = np.array([3, -4, 0, 0], dtype=np.int8)          # norm 5
    b64 = base64.b64encode(raw.tobytes()).decode()
    monkeypatch.setattr(providers, "post_json", _api([b64]))
    v = embedder.embed(["x"])
    assert v.shape == (1, 4)
    assert np.allclose(np.linalg.norm(v[0]), 1.0)
    assert np.allclose(v[0], [0.6, -0.8, 0.0, 0.0])


def test_float_array_also_accepted(embedder, monkeypatch):
    monkeypatch.setattr(providers, "post_json", _api([[1.0, 1.0, 1.0, 1.0]]))
    v = embedder.embed(["x"])
    assert np.allclose(np.linalg.norm(v[0]), 1.0)


def test_disk_cache_prevents_second_request(embedder, monkeypatch):
    calls = []
    monkeypatch.setattr(providers, "post_json", _api([[0.0, 2.0]], calls))
    a = embedder.embed(["same text"])
    b = embedder.embed(["same text"])          # must come from cache
    assert len(calls) == 1
    assert np.allclose(a, b)


def test_batching_splits_requests(embedder, monkeypatch):
    calls = []
    monkeypatch.setattr(providers, "post_json",
                        _api([[1.0, 0.0]] * 3, calls))
    embedder.embed(["a", "b", "c"], batch_size=2)
    assert [len(c) for c in calls] == [2, 1]


def test_dims_assert_when_pinned(embedder, monkeypatch):
    from megabrain.errors import ProviderError
    embedder.dims = 8
    monkeypatch.setattr(providers, "post_json", _api([[1.0, 2.0]]))
    with pytest.raises(ProviderError, match="dims"):
        embedder.embed(["x"])


def test_response_order_by_index(embedder, monkeypatch):
    def fake(path, body, key=None, retries=5, timeout=120, base_url=None):
        return {"data": [{"index": 1, "embedding": [0.0, 5.0]},
                         {"index": 0, "embedding": [5.0, 0.0]}], "usage": {}}
    monkeypatch.setattr(providers, "post_json", fake)
    v = embedder.embed(["first", "second"])
    assert np.allclose(v[0], [1.0, 0.0]) and np.allclose(v[1], [0.0, 1.0])
