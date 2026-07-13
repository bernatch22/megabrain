"""Differential harness for score_chunks — the measured, locked retrieval core.

Turning score_chunks into a self-gating lane pipeline must NOT move a single
score. This snapshots the exact `fused` float array score_chunks produces for a
battery of queries that exercise EVERY lane — short dev query (dense + file
fusion + test penalty + lexical boost), long issue query (>25 ident tokens →
issue-mode RRF + BM25 + traceback grounding + test masking), and a path-scoped
query — over a deterministically-indexed repo. Floats are compared bit-for-bit
(np.array_equal). Any drift fails here.

Regenerate ONLY on an intended change:  RESET_LANES=1 pytest tests/test_scoring_lanes.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np
import pytest

GOLDEN = Path(__file__).parent / "goldens" / "scoring_lanes.json"
DIMS = 256


class _Emb:
    """md5-bucketed token-hash embedder — deterministic across processes."""

    def __init__(self, *a, model: str | None = None, **k):
        self.model = model or "fake/lanes"
        self.cost = 0.0
        self.tokens = 0

    def embed(self, texts, batch_size=None):
        out = np.zeros((len(texts), DIMS), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in re.findall(r"[A-Za-z_]+", t.lower()):
                h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:4], "big")
                out[i, h % DIMS] += 1.0
            n = np.linalg.norm(out[i])
            if n > 0:
                out[i] /= n
        return out


# a repo with a test dir (exercises the test-penalty lane) + a traceback target
_FILES = {
    "auth/login.py": (
        'def login_user(name, password):\n'
        '    """Authenticate a user login with a password check."""\n'
        '    if not validate_password(name, password):\n'
        '        raise ValueError("bad credentials")\n'
        '    return issue_token(name)\n\n\n'
        'def validate_password(name, password):\n'
        '    """Verify the stored password hash for the user."""\n'
        '    return hash(password) % 7 == hash(name) % 7\n'),
    "auth/token.py": (
        'def issue_token(user):\n'
        '    """Mint a signed session token for an authenticated user."""\n'
        '    return {"user": user, "token": "abc"}\n'),
    "billing/invoice.py": (
        'def create_invoice(amount):\n'
        '    """Create a billing invoice for the given amount."""\n'
        '    return {"amount": amount, "status": "open"}\n'),
    "tests/test_login.py": (
        'def test_login_user_ok():\n'
        '    """A valid login returns a token."""\n'
        '    assert login_user("a", "a")\n'),
}

_SHORT = "login user password"
# >25 identifier tokens + a traceback frame -> issue mode
_ISSUE = (
    "Traceback (most recent call last):\n"
    '  File "auth/login.py", line 4, in login_user\n'
    '    raise ValueError("bad credentials")\n'
    "ValueError: bad credentials when authenticate validate password hash user "
    "token session mint signed credential verify stored login flow raises "
    "unexpected exception during password validation in the auth subsystem "
    "when a user submits an invalid password to login_user validate_password")


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    import megabrain.indexing.indexer as indexer
    import megabrain.retrieval.state as state
    monkeypatch.setattr(indexer, "Embedder", _Emb)
    monkeypatch.setattr(state, "Embedder", _Emb)
    for rel, src in _FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(src)
    from megabrain.indexing.indexer import index_repo
    index_repo(tmp_path, quiet=True)
    return tmp_path


def _fused(indexed, query, path_filter=None):
    from megabrain.retrieval.query import load_state, score_chunks
    with load_state(indexed) as st:
        metas, fused = score_chunks(st, query, path_filter=path_filter)
    return [m.file for m in metas], np.asarray(fused, dtype=np.float64)


CASES = {"short": (_SHORT, None), "issue": (_ISSUE, None),
         "scoped": (_SHORT, "auth")}


def test_scoring_is_bit_identical(indexed):
    got = {}
    for key, (q, pf) in CASES.items():
        files, fused = _fused(indexed, q, pf)
        got[key] = {"files": files, "fused": [float(x) for x in fused]}

    if os.environ.get("RESET_LANES"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(got, indent=1))
        pytest.skip("golden regenerated")

    assert GOLDEN.exists(), "run RESET_LANES=1 to create the golden"
    exp = json.loads(GOLDEN.read_text())
    for key in CASES:
        assert got[key]["files"] == exp[key]["files"], f"{key}: candidate set moved"
        # bit-for-bit float equality — a lane refactor must not move any score
        assert np.array_equal(np.array(got[key]["fused"]),
                              np.array(exp[key]["fused"])), f"{key}: score drift"
