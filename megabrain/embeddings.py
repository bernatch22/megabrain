"""Perplexity pplx-embed-v1-0.6b client. Base64(int8) wire format, L2-normalized.
Disk cache under ~/.megabrain/cache keyed by sha1(model + text)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path

import numpy as np

MODEL = "pplx-embed-v1-0.6b"
DIMS = 1024
URL = "https://api.perplexity.ai/v1/embeddings"
CACHE = Path.home() / ".megabrain/cache" / MODEL


def _find_key() -> str:
    k = os.environ.get("PERPLEXITY_API_KEY")
    if k:
        return k
    zshrc = Path.home() / ".zshrc"
    if zshrc.exists():
        import re
        m = re.search(r'^export PERPLEXITY_API_KEY=["\']?([^"\'\s#]+)',
                      zshrc.read_text(), re.M)
        if m:
            return m.group(1)
    raise RuntimeError("PERPLEXITY_API_KEY not set (env or ~/.zshrc)")


class PplxEmbedder:
    def __init__(self, api_key: str | None = None):
        self.key = api_key or _find_key()
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
        return np.stack(out) if out else np.zeros((0, DIMS))  # type: ignore[arg-type]

    def _request(self, batch: list[str], retries: int = 5) -> list[np.ndarray]:
        body = json.dumps({"model": MODEL, "input": batch}).encode()
        for attempt in range(retries):
            req = urllib.request.Request(
                URL, data=body, method="POST",
                headers={"Authorization": f"Bearer {self.key}",
                         "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as res:
                    d = json.loads(res.read())
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"pplx {e.code}: {e.read()[:200]}") from e
        u = d.get("usage", {})
        self.tokens += u.get("total_tokens", 0)
        self.cost += u.get("cost", {}).get("total_cost", 0.0)
        vecs = []
        for r in sorted(d["data"], key=lambda r: r["index"]):
            e = r["embedding"]
            v = (np.frombuffer(base64.b64decode(e), dtype=np.int8).astype(np.float32)
                 if isinstance(e, str) else np.array(e, dtype=np.float32))
            if len(v) != DIMS:
                raise ValueError(f"expected {DIMS} dims, got {len(v)}")
            n = np.linalg.norm(v)
            vecs.append(v / n if n > 0 else v)
        return vecs
