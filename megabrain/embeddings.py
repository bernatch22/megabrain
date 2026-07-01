"""Embedding client over OpenRouter (OpenAI-compatible /embeddings).

Default model perplexity/pplx-embed-v1-0.6b — 1024-dim, base64(int8) wire
format, L2-normalized here. Any OpenRouter embedding model works via
MEGABRAIN_EMBED_MODEL (int8-base64 OR float arrays are both decoded). Disk
cache under ~/.megabrain/cache keyed by sha1(model + text), so re-indexing a
near-identical checkout only re-embeds changed content."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

import numpy as np

from . import providers

MODEL = providers.EMBED_MODEL
# 0 = infer the dimension from the model's own output (any OpenRouter embedding
# model works). Pin MEGABRAIN_EMBED_DIMS only to assert an expected width.
DIMS = int(os.environ.get("MEGABRAIN_EMBED_DIMS", "0"))
CACHE = Path.home() / ".megabrain/cache" / MODEL.replace("/", "_")


class Embedder:
    def __init__(self, api_key: str | None = None):
        self.key = api_key or providers.find_embed_key()
        CACHE.mkdir(parents=True, exist_ok=True)
        self.cost = 0.0
        self.tokens = 0

    def _cpath(self, text: str) -> Path:
        h = hashlib.sha1(f"{MODEL}\x00{text}".encode()).hexdigest()
        return CACHE / f"{h}.npy"

    def embed(self, texts: list[str], batch_size: int = 64) -> np.ndarray:
        out: list[np.ndarray | None] = [None] * len(texts)
        missing = []
        for i, t in enumerate(texts):
            p = self._cpath(t)
            if p.exists():
                out[i] = np.load(p)
            else:
                missing.append(i)
        for s in range(0, len(missing), batch_size):
            idxs = missing[s:s + batch_size]
            vecs = self._request([texts[i] for i in idxs])
            for i, v in zip(idxs, vecs):
                np.save(self._cpath(texts[i]), v)
                out[i] = v
        return np.stack(out) if out else np.zeros((0, DIMS or 1024))  # type: ignore[arg-type]

    def _request(self, batch: list[str]) -> list[np.ndarray]:
        d = providers.post_json("/embeddings", {"model": MODEL, "input": batch},
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
            if DIMS and len(v) != DIMS:
                raise ValueError(f"expected {DIMS} dims, got {len(v)} "
                                 f"(model {MODEL} — unset/adjust MEGABRAIN_EMBED_DIMS)")
            n = np.linalg.norm(v)
            vecs.append(v / n if n > 0 else v)
        return vecs


# Back-compat alias (older imports referenced the pplx-specific name).
PplxEmbedder = Embedder
