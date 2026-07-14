"""Golden-text snapshots: pin the RENDERED BYTES of the engine's outputs.

The metric gates (R@1, bundle_full) are blind to ordering churn inside a
passing threshold and to render regressions. These snapshots pin the exact
text of render(), render_pruned() and the bundle/selection structure over a
deterministic corpus, so any refactor that changes output bytes fails loudly.

Unlike conftest.FakeEmbedder (which uses Python's per-process salted hash()),
DeterministicEmbedder buckets tokens by md5 — identical vectors across
processes and runs, so the goldens are byte-stable.

Regenerate after an INTENDED output change:  RESET_GOLDENS=1 pytest tests/test_golden_render.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np
import pytest

DIMS = 256
GOLDEN_DIR = Path(__file__).parent / "goldens"


class DeterministicEmbedder:
    """Like conftest.FakeEmbedder but md5-bucketed: stable across processes."""

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 **_kw):
        self.model = model or "fake/md5-deterministic"
        self.cost = 0.0
        self.tokens = 0

    def embed(self, texts: list[str], batch_size: int = 64) -> np.ndarray:
        out = np.zeros((len(texts), DIMS), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in re.findall(r"[A-Za-z_]+", t.lower()):
                h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:4], "big")
                out[i, h % DIMS] += 1.0
            n = np.linalg.norm(out[i])
            if n > 0:
                out[i] /= n
        return out


@pytest.fixture
def golden_repo(tmp_path, monkeypatch):
    """A 4-file repo indexed with the deterministic embedder. Slightly richer
    than conftest.tiny_repo: a class file exercises multi-chunk CORE and the
    symbol outline; cross-file imports exercise the graph lane."""
    import megabrain.indexing.indexer as indexer
    import megabrain.retrieval.state as state
    monkeypatch.setattr(indexer, "Embedder", DeterministicEmbedder)
    monkeypatch.setattr(state, "Embedder", DeterministicEmbedder)

    (tmp_path / "auth").mkdir()
    (tmp_path / "auth" / "login.py").write_text(
        'from auth.session import open_session\n\n\n'
        'def login_user(name, password):\n'
        '    """Authenticate a user login with password check."""\n'
        '    if check_password(name, password):\n'
        '        return open_session(name)\n'
        '    return None\n\n\n'
        'def check_password(name, password):\n'
        '    """Verify the stored password hash for the user."""\n'
        '    return hash(password) % 7 == hash(name) % 7\n')
    (tmp_path / "auth" / "session.py").write_text(
        'class Session:\n'
        '    """A logged-in user session with an expiry."""\n\n'
        '    def __init__(self, user):\n'
        '        self.user = user\n'
        '        self.expired = False\n\n'
        '    def expire(self):\n'
        '        """Mark the session expired (logout)."""\n'
        '        self.expired = True\n\n\n'
        'def open_session(user):\n'
        '    """Create a fresh session for an authenticated user."""\n'
        '    return Session(user)\n')
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
    index_repo(tmp_path)
    return tmp_path


def _normalize(res: dict) -> dict:
    """Strip run-varying fields: wall-clock ms and the tmp-dir repo name."""
    res = dict(res)
    res["ms"] = 0
    res["repo"] = "goldenrepo"
    return res


def _check(name: str, text: str) -> None:
    path = GOLDEN_DIR / name
    if os.environ.get("RESET_GOLDENS"):
        GOLDEN_DIR.mkdir(exist_ok=True)
        path.write_text(text)
        return
    assert path.exists(), f"golden missing: {path} — run RESET_GOLDENS=1 to create"
    expected = path.read_text()
    assert text == expected, (
        f"golden mismatch: {name}\n"
        f"--- expected ---\n{expected}\n--- got ---\n{text}\n"
        f"(intended change? RESET_GOLDENS=1 pytest {__file__})")


QUERIES = {
    "login": "user login password check",
    "invoice": "create billing invoice",
    "session": "how does a session expire on logout",
}


def test_render_goldens(golden_repo):
    from megabrain.retrieval.bundle import search
    from megabrain.retrieval.render import render
    for key, q in QUERIES.items():
        res = _normalize(search(golden_repo, q))
        _check(f"render_{key}.md", render(res))
    # compact + full variants pin the render flags on one query
    res = _normalize(search(golden_repo, QUERIES["login"]))
    _check("render_login_compact.md", render(res, compact=True))
    _check("render_login_full.md", render(res, related_code=True))


def test_render_pruned_golden(golden_repo):
    from megabrain.retrieval.bundle import prune_search_root
    from megabrain.retrieval.render import render_pruned
    res = _normalize(prune_search_root(golden_repo, QUERIES["login"],
                                       include_pruned=True))
    _check("render_pruned_login.md", render_pruned(res))
    # structural pin: signal/noise ids + counts (text stripped for brevity)
    slim = {"kept": res["kept"], "pruned": res["pruned"], "scanned": res["scanned"],
            "chunks": [{k: c[k] for k in ("id", "file", "start_line", "end_line",
                                          "kind", "name", "score")}
                       for c in res["chunks"]],
            "noise": [{k: c[k] for k in ("id", "file", "start_line", "end_line")}
                      for c in res.get("noise", [])]}
    _check("prune_structure_login.json", json.dumps(slim, indent=1, sort_keys=True))


def test_bundle_structure_golden(golden_repo):
    """Pin the bundle SHAPE (files, order, chunk spans, selection) as JSON —
    the contract every frontend + ask consume."""
    from megabrain.retrieval.bundle import chunks_for_file_root, search
    res = _normalize(search(golden_repo, QUERIES["login"]))
    shape = {
        "tier1": [{"file": t["file"], "score": round(t["score"], 4),
                   "chunks": [(c["id"], c["start_line"], c["end_line"]) for c in t["chunks"]],
                   "neighbors": t["neighbors"]} for t in res["tier1"]],
        "tier2": [{"file": t["file"], "via_graph": t["via_graph"],
                   "matched": t["matched"],
                   "best_chunk": t["best_chunk"]["id"] if t.get("best_chunk") else None}
                  for t in res["tier2"]],
    }
    _check("bundle_login.json", json.dumps(shape, indent=1, sort_keys=True))

    sel = chunks_for_file_root(golden_repo, "auth/login.py", QUERIES["login"])
    slim = {"role": sel["role"], "selected_count": sel["selected_count"],
            "chunks": [{"id": c["id"], "selected": c["selected"],
                        "span": [c["start_line"], c["end_line"]]}
                       for c in sel["chunks"]]}
    _check("selection_login.json", json.dumps(slim, indent=1, sort_keys=True))


def test_get_code_golden(golden_repo):
    from megabrain.retrieval.files import get_code
    _check("get_code_symbol.md", get_code(golden_repo, "auth/session.py", "Session.expire"))
    _check("get_code_file.md", get_code(golden_repo, "util.py"))
    # the containment guard is part of the pinned contract
    assert get_code(golden_repo, "../../etc/passwd").startswith("not found")
