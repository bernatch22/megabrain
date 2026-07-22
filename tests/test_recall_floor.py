"""The recall floor: fusion is a ranking opinion, never a recall gate.

Field case (nx#35656 demo): the prior-art file the agent most needed —
project-glob-changes.ts, a 48-line helper inside ANOTHER feature's directory —
sat at raw-dense rank 13 of 10,891 and file fusion pushed it to 81, out of the
bundle. The agent found it with plain grep and scored the tool 7/10 for
exactly this. Reusable logic lives under a different name in a different
subsystem BY CONSTRUCTION, so the ranking was structurally biased against the
one lookup (RULE-4 "don't duplicate logic") where retrieval matters most.

The floor: every non-test file owning a raw-dense top-N chunk is present in
the bundle. Pure additions to the RELATED tail (same stance as the flow lane)
— never ranking, never displacing, bundle_full can only rise."""

from dataclasses import replace

from megabrain.retrieval.bundle import prune_search, search_with_state
from megabrain.retrieval.state import load_state

# The query shares its tokens exactly with the buried helper's chunk.
QUERY = "where is the zorblatt glob matcher for plugin patterns"


def _bundle_files(res):
    return [t["file"] for t in res["tier1"]] + [t["file"] for t in res["tier2"]]


def _floor_files(res):
    return [t["file"] for t in res["tier2"] if t.get("via_floor")]


import pytest


@pytest.fixture
def buried_repo(tmp_path, fake_embedder):
    """A repo engineered to reproduce the nx burial with token-hash cosine:

    - 14 decoy files whose EVERY chunk and skeleton scream the query's
      vocabulary -> file fusion ranks all of them above the target's file.
    - 1 target helper whose single chunk matches the query maximally, buried
      in a file whose OTHER content (and thus skeleton) is about something
      else entirely — the shape of prior art."""
    for i in range(14):
        (tmp_path / f"glob_area_{i:02}.py").write_text(
            f'def glob_matcher_{i}(plugin, patterns):\n'
            f'    """Glob matcher for plugin patterns matching."""\n'
            f'    return plugin, patterns\n\n\n'
            f'def plugin_patterns_{i}(glob, matcher):\n'
            f'    """Plugin patterns glob matcher helper."""\n'
            f'    return glob, matcher\n')
    (tmp_path / "orchard").mkdir()
    (tmp_path / "orchard" / "kumquat_scheduler.py").write_text(
        'def prune_orchard(trees, season):\n'
        '    """Seasonal kumquat orchard pruning rotation."""\n'
        '    return [t for t in trees if t.season == season]\n\n\n'
        'def zorblatt(glob, matcher, plugin, patterns):\n'
        '    """The zorblatt glob matcher for plugin patterns."""\n'
        '    return matcher(glob, plugin, patterns)\n\n\n'
        'def harvest_rotation(trees):\n'
        '    """Kumquat harvest rotation ordering by ripeness."""\n'
        '    return sorted(trees, key=lambda t: t.ripeness)\n')
    from megabrain.indexing.indexer import index_repo
    index_repo(tmp_path)
    return tmp_path


def test_fusion_buries_the_prior_art_without_the_floor(buried_repo):
    """The control arm: with the floor disabled, the decoys' file-level score
    crowds the helper's file out of the bundle — the nx#35656 failure shape.
    (If this ever starts passing, the fixture no longer reproduces the burial
    and the floor test below proves nothing — fix the fixture first.)"""
    with load_state(buried_repo) as st:
        st.params = replace(st.params, recall_floor_top=0)
        res = search_with_state(st, QUERY)
    assert not any("kumquat_scheduler" in f for f in _bundle_files(res)), \
        "fixture no longer reproduces the burial — floor tests are void"


def test_floor_restores_the_buried_file(buried_repo):
    with load_state(buried_repo) as st:
        res = search_with_state(st, QUERY)
    floored = _floor_files(res)
    assert any("kumquat_scheduler" in f for f in floored)
    # and it flows through to the prune signal list (what the MCP tool returns)
    with load_state(buried_repo) as st:
        pr = prune_search(st, QUERY, with_text=False)
    assert any("kumquat_scheduler" in c["file"] for c in pr["chunks"])


def test_floor_is_purely_additive(buried_repo):
    """Floor ON vs OFF: CORE is identical and no file is ever lost.

    The invariant is set CONTAINMENT of the whole bundle, not list equality of
    the RELATED tail — because two additive lanes can trade provenance. Found
    in the 18-repo eval (shipway / "how does logging get configured"): the
    flow lane had been adding src/config/schema.ts; with the floor on, the
    floor claimed it first, the flow lane's capped budget went to a DIFFERENT
    file instead, and the bundle came out one file LARGER. Asserting list
    equality called that a violation when the engine was right — so assert
    what actually matters: CORE untouched, nothing dropped."""
    with load_state(buried_repo) as st:
        st.params = replace(st.params, recall_floor_top=0)
        off = search_with_state(st, QUERY)
    with load_state(buried_repo) as st:
        on = search_with_state(st, QUERY)
    assert [t["file"] for t in off["tier1"]] == [t["file"] for t in on["tier1"]]
    assert set(_bundle_files(off)) <= set(_bundle_files(on))
    assert len(_bundle_files(on)) > len(_bundle_files(off))   # it did add


def test_floor_never_lifts_test_files(buried_repo, tmp_path):
    """A test file in the raw top-N stays out of the floor: the test penalty
    and issue-mode masking down-weight tests on purpose, and the rerank's
    tests tail already surfaces them as the spec."""
    (buried_repo / "tests").mkdir(exist_ok=True)
    (buried_repo / "tests" / "test_zorblatt.py").write_text(
        'def test_zorblatt_glob_matcher_plugin_patterns():\n'
        '    """The zorblatt glob matcher for plugin patterns, pinned."""\n'
        '    assert True\n')
    from megabrain.indexing.indexer import index_repo
    index_repo(buried_repo)
    with load_state(buried_repo) as st:
        res = search_with_state(st, QUERY)
    assert not any("test_zorblatt" in f for f in _floor_files(res))
