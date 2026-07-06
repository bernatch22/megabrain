"""PATH-SCOPE: root resolution + sub-path retrieval filtering.

Unit tests (root resolution, filter helper) need no corpus. The end-to-end
retrieval checks run only when an indexed repo is available (~/pinecall/sdk),
scoped to a real sub-path (src/dispatch). They are skipped otherwise so the
gate stays green on machines without the corpus.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from megabrain.query import _apply_path_filter, _under_path, search
from megabrain.store import resolve_root

SDK = Path("~/pinecall/sdk").expanduser()
HAS_SDK = (SDK / ".megabrain" / "db.sqlite").exists()
SUBDIR = "src/dispatch"


# ── root resolution (no corpus) ────────────────────────────────────────────

def test_resolve_root_walks_up(tmp_path: Path):
    root = tmp_path / "repo"
    (root / ".megabrain").mkdir(parents=True)
    (root / ".megabrain" / "db.sqlite").write_text("")
    nested = root / "src" / "dispatch"
    nested.mkdir(parents=True)

    # a nested sub-path resolves to the root + POSIX subpath
    r, sub = resolve_root(nested)
    assert r == root.resolve()
    assert sub == "src/dispatch"

    # the root itself -> empty subpath (no filter)
    r2, sub2 = resolve_root(root)
    assert r2 == root.resolve()
    assert sub2 == ""


def test_resolve_root_no_index_raises(tmp_path: Path):
    with pytest.raises(ValueError):
        resolve_root(tmp_path / "nope" / "deeper")


# ── directory-boundary-aware match + filter (no corpus) ────────────────────

def test_under_path_boundary():
    assert _under_path("src/dispatch/run.ts", "src/dispatch")
    assert _under_path("src/dispatch", "src/dispatch")          # filter IS a file
    assert not _under_path("src/dispatcher.ts", "src/dispatch")  # prefix, not dir
    assert _under_path("anything", "")                           # empty = all


def test_apply_path_filter_scopes_metas():
    metas = [{"file": "src/dispatch/a.ts"}, {"file": "src/dispatcher.ts"},
             {"file": "src/other/b.ts"}]
    M = np.arange(len(metas) * 2, dtype=np.float32).reshape(len(metas), 2)
    fm, fM = _apply_path_filter(metas, M, "src/dispatch")
    assert [m["file"] for m in fm] == ["src/dispatch/a.ts"]
    assert fM.shape == (1, 2)
    assert np.array_equal(fM[0], M[0])


def test_apply_path_filter_none_is_identity():
    metas = [{"file": "a.ts"}]
    M = np.zeros((1, 2), dtype=np.float32)
    fm, fM = _apply_path_filter(metas, M, None)
    assert fm is metas and fM is M


def test_apply_path_filter_fail_open_on_no_match():
    metas = [{"file": "a.ts"}]
    M = np.zeros((1, 2), dtype=np.float32)
    fm, fM = _apply_path_filter(metas, M, "does/not/exist")
    assert fm is metas and fM is M   # unfiltered, not empty


# ── end-to-end on a real indexed repo (skipped without corpus) ─────────────

skip_no_sdk = pytest.mark.skipif(not HAS_SDK, reason="~/pinecall/sdk not indexed")


def _bundle_files(res: dict) -> list[str]:
    return [t["file"] for t in res["tier1"]] + [t["file"] for t in res["tier2"]]


@skip_no_sdk
def test_subpath_bundle_stays_under_subpath():
    root, sub = resolve_root(SDK / SUBDIR)
    assert sub == SUBDIR
    res = search(root, "how does dispatch work", path_filter=sub)
    files = _bundle_files(res)
    assert files, "expected a non-empty scoped bundle"
    for f in files:
        assert _under_path(f, SUBDIR), f"{f} escaped the sub-path scope"


@skip_no_sdk
def test_root_query_unfiltered_matches_no_filter():
    root, sub = resolve_root(SDK)
    assert sub == ""
    a = search(root, "how does dispatch work")
    b = search(root, "how does dispatch work", path_filter=None)
    assert _bundle_files(a) == _bundle_files(b)
    # empty-string filter is also a no-op (== None path)
    c = search(root, "how does dispatch work", path_filter="")
    assert _bundle_files(a) == _bundle_files(c)
