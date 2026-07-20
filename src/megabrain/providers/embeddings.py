"""Embedding client over any OpenAI-compatible /embeddings endpoint.

Default model perplexity/pplx-embed-v1-0.6b — 1024-dim, base64(int8) wire
format, L2-normalized here. Any OpenRouter embedding model works via
MEGABRAIN_EMBED_MODEL (int8-base64 OR float arrays are both decoded). Disk
cache under ~/.megabrain/cache keyed by sha1(model + text), so re-indexing a
near-identical checkout only re-embeds changed content.

Config is resolved at CONSTRUCTION time (per Embedder instance), not import
time: setting MEGABRAIN_EMBED_MODEL after import works, tests inject without
monkeypatching module globals, and two Embedders with different models can
coexist in one process (forge_eval sweeps, bakeoffs).
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

import numpy as np

from .. import providers


def model_name() -> str:
    """The embedding model slug — read per call so env swaps are honest."""
    return os.environ.get("MEGABRAIN_EMBED_MODEL", "perplexity/pplx-embed-v1-0.6b")


class Embedder:
    def __init__(self, api_key: str | None = None, model: str | None = None,
                 dims: int | None = None, batch: int | None = None,
                 cache_dir: Path | None = None):
        self.key = api_key or providers.find_embed_key()
        self.model = model or model_name()
        # 0 = infer the dimension from the model's own output. Pin
        # MEGABRAIN_EMBED_DIMS only to assert an expected width.
        self.dims = dims if dims is not None else \
            int(os.environ.get("MEGABRAIN_EMBED_DIMS", "0"))
        # Batch size per /embeddings request. Local servers (Ollama on a
        # laptop) can choke on 64 large code chunks — drop to 4-8 there.
        self.batch = batch if batch is not None else \
            int(os.environ.get("MEGABRAIN_EMBED_BATCH", "64"))
        # …but the request is ALSO capped by total tokens across the batch, and
        # that cap is what a fixed item count cannot respect: pplx-embed rejects
        # a request over 120k tokens ("Input total size exceeds maximum number
        # of allowed tokens: got 252064, maximum is 120000"), which 64 large
        # markdown chunks blow past on the first batch. Repos of small files
        # never hit it, so it surfaced only when a docs-heavy repo was indexed.
        self.max_tokens = int(os.environ.get("MEGABRAIN_EMBED_MAX_TOKENS", "100000"))
        self.cache = Path(cache_dir) if cache_dir is not None else \
            Path.home() / ".megabrain/cache" / self.model.replace("/", "_")
        self.cache.mkdir(parents=True, exist_ok=True)
        self.cost = 0.0
        self.tokens = 0

    def _cpath(self, text: str) -> Path:
        h = hashlib.sha1(f"{self.model}\x00{text}".encode()).hexdigest()
        return self.cache / f"{h}.npy"

    # Deliberately pessimistic: the request that hit the cap measured 2.83
    # chars/token, and denser scripts go lower still. Over-splitting costs one
    # extra HTTP round trip; under-splitting fails the whole index.
    CHARS_PER_TOKEN = 2.5

    def _batches(self, idxs: list[int], texts: list[str], batch_size: int):
        """Group indices into requests bounded by BOTH the item count and the
        token budget. A single text over budget goes alone rather than being
        dropped — the chunker bounds chunk size, so the provider still accepts
        it, and failing loudly beats silently skipping content."""
        cur: list[int] = []
        cur_tok = 0.0
        for i in idxs:
            tok = len(texts[i]) / self.CHARS_PER_TOKEN
            if cur and (len(cur) >= batch_size or cur_tok + tok > self.max_tokens):
                yield cur
                cur, cur_tok = [], 0.0
            cur.append(i)
            cur_tok += tok
        if cur:
            yield cur

    def embed(self, texts: list[str], batch_size: int | None = None) -> np.ndarray:
        batch_size = batch_size or self.batch
        out: list[np.ndarray | None] = [None] * len(texts)
        missing = []
        for i, t in enumerate(texts):
            p = self._cpath(t)
            if p.exists():
                out[i] = np.load(p)
            else:
                missing.append(i)
        for idxs in self._batches(missing, texts, batch_size):
            vecs = self._request([texts[i] for i in idxs])
            for i, v in zip(idxs, vecs):
                p = self._cpath(texts[i])
                tmp = p.with_name(f"{p.stem}.{os.getpid()}.tmp.npy")
                np.save(tmp, v)
                tmp.replace(p)   # atomic: concurrent readers never see a partial file
                out[i] = v
        return np.stack(out) if out else np.zeros((0, self.dims or 1024))  # type: ignore[arg-type]

    def _request(self, batch: list[str]) -> list[np.ndarray]:
        d = providers.post_json("/embeddings", {"model": self.model, "input": batch},
                                self.key, retries=5, timeout=120,
                                base_url=providers.EMBED_BASE_URL)
        u = d.get("usage", {})
        self.tokens += u.get("total_tokens", 0)
        cost = u.get("cost")
        self.cost += cost.get("total_cost", 0.0) if isinstance(cost, dict) else (cost or 0.0)
        vecs = []
        for r in sorted(d["data"], key=lambda r: r["index"]):
            e = r["embedding"]
            v = (np.frombuffer(base64.b64decode(e), dtype=np.int8).astype(np.float32)
                 if isinstance(e, str) else np.array(e, dtype=np.float32))
            if self.dims and len(v) != self.dims:
                from ..errors import ProviderError
                raise ProviderError(
                    f"expected {self.dims} dims, got {len(v)} (model {self.model} "
                    f"— unset/adjust MEGABRAIN_EMBED_DIMS)")
            n = np.linalg.norm(v)
            vecs.append(v / n if n > 0 else v)
        return vecs
