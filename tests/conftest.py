"""Shared fixtures: a deterministic offline embedder + a tiny indexed repo.

FakeEmbedder maps text to a bag-of-tokens hash vector (similar text -> similar
vector), so retrieval BEHAVES realistically with no network and no API key —
end-to-end index/search tests run anywhere (CI, contributor laptops).
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

DIMS = 256


def _bucket(tok: str) -> int:
    """Deterministic token bucket. md5, NOT builtin hash(): str hash is salted
    per process, which made near-threshold similarity tests (e.g. flow attach
    cutoffs) flaky depending on the interpreter's seed."""
    return int.from_bytes(hashlib.md5(tok.encode()).digest()[:4], "big") % DIMS


@pytest.fixture(autouse=True)
def _pin_chat_provider(monkeypatch):
    """Isolate the suite from the ambient chat provider: default to OpenRouter
    (the network-mockable path) so tests are deterministic whether or not the
    Claude SDK happens to be installed. Claude-provider tests override this."""
    monkeypatch.setenv("MEGABRAIN_CHAT_PROVIDER", "openrouter")


class FakeEmbedder:
    """Drop-in for megabrain.embeddings.Embedder: token-hash embeddings."""

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 **_kw):
        import os
        # honor the same construction-time config as the real Embedder so
        # embed-model-change tests can flip MEGABRAIN_EMBED_MODEL
        self.model = model or os.environ.get("MEGABRAIN_EMBED_MODEL",
                                             "fake/token-hash")
        self.cost = 0.0
        self.tokens = 0

    def embed(self, texts: list[str], batch_size: int = 64) -> np.ndarray:
        out = np.zeros((len(texts), DIMS), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in re.findall(r"[A-Za-z_]+", t.lower()):
                out[i, _bucket(tok)] += 1.0
            n = np.linalg.norm(out[i])
            if n > 0:
                out[i] /= n
        self.tokens += sum(len(t.split()) for t in texts)
        return out


@pytest.fixture
def fake_embedder(monkeypatch):
    """Patch the engine to use FakeEmbedder everywhere (indexer + query)."""
    import megabrain.indexing.indexer as indexer
    import megabrain.retrieval.state as state
    monkeypatch.setattr(indexer, "Embedder", FakeEmbedder)
    # Embedder moved to retrieval.state in the query.py split; load_state()
    # resolves it there.
    monkeypatch.setattr(state, "Embedder", FakeEmbedder)
    return FakeEmbedder


@pytest.fixture
def tiny_repo(tmp_path, fake_embedder):
    """A 3-file python repo, indexed with the fake embedder."""
    (tmp_path / "auth").mkdir()
    (tmp_path / "auth" / "login.py").write_text(
        'def login_user(name, password):\n'
        '    """Authenticate a user login with password check."""\n'
        '    return check_password(name, password)\n\n\n'
        'def check_password(name, password):\n'
        '    """Verify the stored password hash for the user."""\n'
        '    return hash(password) % 7 == hash(name) % 7\n')
    (tmp_path / "billing").mkdir()
    (tmp_path / "billing" / "invoice.py").write_text(
        'def create_invoice(amount):\n'
        '    """Create a billing invoice for the given amount."""\n'
        '    return {"amount": amount, "status": "open"}\n')
    (tmp_path / "util.py").write_text(
        'def flatten(xs):\n'
        '    """Flatten a nested list one level."""\n'
        '    return [y for x in xs for y in x]\n')
    from megabrain.indexing.indexer import index_repo
    index_repo(tmp_path, quiet=True)
    return tmp_path
