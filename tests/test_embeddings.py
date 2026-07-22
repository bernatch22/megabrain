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


# ---------------------------------------------------------- token budgeting

def test_batches_split_on_token_budget_not_just_count(embedder):
    """A fixed item count cannot respect a per-request TOKEN cap. 64 large
    chunks exceeded pplx-embed's 120k-token limit on the first batch and killed
    a whole index run; small-file repos never hit it."""
    embedder.max_tokens = 1000                      # 2500 chars at 2.5 ch/token
    texts = ["x" * 1000] * 10                       # 400 tokens each
    groups = list(embedder._batches(list(range(10)), texts, batch_size=64))
    assert len(groups) > 1, "one oversized request instead of several"
    for g in groups:
        assert sum(len(texts[i]) for i in g) / embedder.CHARS_PER_TOKEN <= 1000
    assert [i for g in groups for i in g] == list(range(10)), "lost or reordered"


def test_batches_still_respect_the_item_count(embedder):
    embedder.max_tokens = 10_000_000
    texts = ["x"] * 10
    assert [len(g) for g in embedder._batches(list(range(10)), texts, 4)] == [4, 4, 2]


def test_a_single_oversized_text_goes_alone_not_dropped(embedder):
    embedder.max_tokens = 100
    texts = ["y" * 100_000, "z"]
    groups = list(embedder._batches([0, 1], texts, batch_size=64))
    assert groups == [[0], [1]]


def test_embed_splits_the_request_and_keeps_order(embedder, monkeypatch):
    embedder.max_tokens = 1000
    calls = []
    monkeypatch.setattr(providers, "post_json",
                        _api([[1.0, 0.0], [0.0, 1.0], [1.0, 0.0], [0.0, 1.0]], calls))
    out = embedder.embed(["a" * 2000, "b" * 2000, "c" * 2000])
    assert len(calls) == 3, f"expected one request per budgeted batch, got {calls}"
    assert out.shape == (3, 2)


# ------------------------------------------------- the bisect net (nx field case)

NX_400 = ('openrouter 400: HTTP 400: {"error":{"message":"failed to get '
          'embeddings: POST /v1/embeddings: 400 Bad Request: Input total size '
          'exceeds maximum number of allowed tokens: got 131400, maximum is '
          '120000"}}')


def _capped_api(max_texts, calls):
    """Fake provider that rejects any batch above `max_texts` inputs with the
    REAL error the nx index died on — the estimate said 100k tokens, the
    provider counted 131k."""
    from megabrain.errors import ProviderError

    def fake(path, body, key=None, retries=5, timeout=120, base_url=None):
        batch = list(body["input"])
        calls.append(batch)
        if len(batch) > max_texts:
            raise ProviderError(NX_400)
        return {"data": [{"index": i, "embedding": [float(len(t)), 1.0]}
                         for i, t in enumerate(batch)],
                "usage": {"total_tokens": 3, "cost": {"total_cost": 0.001}}}
    return fake


def test_token_cap_400_bisects_instead_of_dying(embedder, monkeypatch):
    """CHARS_PER_TOKEN is per-language wrong by construction; a wrong estimate
    must cost extra round trips, never the whole index."""
    calls = []
    monkeypatch.setattr(providers, "post_json", _capped_api(2, calls))
    texts = [c * (i + 1) for i, c in enumerate("abcdefg")]  # one 7-text batch
    out = embedder.embed(texts)
    assert out.shape == (7, 2)
    # order preserved: row i encodes len(texts[i]) in its component ratio
    assert [round(float(r[0] / r[1])) for r in out] == [1, 2, 3, 4, 5, 6, 7]
    # bisection tree: 7 -> 3+4 -> 1+2 / 2+2; failures retried as halves
    assert sorted(len(c) for c in calls) == [1, 2, 2, 2, 3, 4, 7]


def test_single_oversized_text_still_raises(embedder, monkeypatch):
    """A single text over the cap is a chunker-bounds violation — loud, not
    silently skipped (completeness is the contract)."""
    from megabrain.errors import ProviderError
    calls = []
    monkeypatch.setattr(providers, "post_json", _capped_api(0, calls))
    with pytest.raises(ProviderError, match="exceeds maximum"):
        embedder.embed(["x" * 50])


def test_non_token_errors_never_bisect(embedder, monkeypatch):
    """Only the token-cap family splits; an auth error must surface on the
    first try, not after log2(n) pointless retries."""
    from megabrain.errors import ProviderError
    calls = []

    def auth_fail(path, body, key=None, retries=5, timeout=120, base_url=None):
        calls.append(1)
        raise ProviderError("openrouter 401: Missing Authentication header")
    monkeypatch.setattr(providers, "post_json", auth_fail)
    with pytest.raises(ProviderError, match="401"):
        embedder.embed(["a" * 10, "b" * 10, "c" * 10])
    assert len(calls) == 1
