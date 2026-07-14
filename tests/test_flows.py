"""Flow cache (self-caching workflow retrieval): write path, paraphrase
retrieval, pure-addition RELATED behavior, sha invalidation, dedup, kill
switch. Offline: FakeEmbedder everywhere, no LLM anywhere (the read path is
cosine-only by design — hard rule 1)."""

import pytest

from megabrain import flows as flows_mod
from megabrain.flows import cache_flow
from megabrain.indexing.indexer import index_repo
from megabrain.retrieval.bundle import search_with_state
from megabrain.retrieval.render import render
from megabrain.retrieval.state import load_state
from megabrain.store import Store

VAD = ('def detect_voice(frame):\n    """barge in detection threshold."""\n'
       "    return frame.energy > 300\n")
TURN = ('def on_vad_start(state):\n    """barge in: interrupt the speaking bot."""\n'
        "    state.cancel_tts()\n")
OTHER = "def unrelated_billing(x):\n    return x * 42\n"

FLOW_Q = "where is barge in handled when the user interrupts the bot"
# a RENDERED answer: prose + real code blocks (what render_ask produces and what
# cache_flow now stores, so a near-exact question can be served verbatim).
FLOW_TEXT = (
    "## Barge-in flow\nVoice activity detection accumulates energy then interrupts "
    "the speaking bot and cancels tts.\n\n"
    "**`vad.py` L1-3**\n```python\n" + VAD + "```\n\n"
    "**`turn.py` L1-3**\n```python\n" + TURN + "```\n")


@pytest.fixture
def repo(tmp_path, fake_embedder):
    (tmp_path / "vad.py").write_text(VAD)
    (tmp_path / "turn.py").write_text(TURN)
    (tmp_path / "billing.py").write_text(OTHER)
    index_repo(tmp_path, quiet=True)
    flows_mod.set_enabled(tmp_path, True)     # opt-in mode; off by default
    return tmp_path


def _cache(repo):
    from tests.conftest import FakeEmbedder
    return cache_flow(repo, FLOW_Q, FLOW_TEXT, ["vad.py", "turn.py"],
                      emb=FakeEmbedder())


def test_cache_and_paraphrase_retrieval(repo):
    assert _cache(repo) is not None
    st = load_state(repo)
    assert len(st.flows) == 1
    # a re-worded question about the same workflow retrieves the cached flow
    res = search_with_state(st, "how does the bot get interrupted on barge in")
    assert res["flows"], "cached flow should match a paraphrase"
    fl = res["flows"][0]
    assert fl["question"] == FLOW_Q
    assert fl["files"] == ["turn.py", "vad.py"]
    assert "```" in fl["text"], "the rendered answer (with code) is stored for serving"
    # and it renders as a labeled section, never silently
    assert "KNOWN FLOW" in render(res)


def _matched_flow(repo, score):
    """A match_flows-shaped entry with REAL shas of the repo's current files.
    qscore constructed (FakeEmbedder can't reproduce pplx): serve reads the
    QUESTION-ONLY lane — identical question ≈ 1.0, a paraphrased question sits
    well under FLOW_SERVE_SIM=0.88, so prose length can't dilute the signal."""
    import hashlib
    sha = {f: hashlib.sha256((repo / f).read_text().encode()).hexdigest()
           for f in ("vad.py", "turn.py")}
    return [{"question": FLOW_Q, "text": FLOW_TEXT,
             "files": ["turn.py", "vad.py"], "sha": sha,
             "score": score, "qscore": score}]


def test_near_exact_question_is_served_without_llm(repo):
    """score >= FLOW_SERVE_SIM + unchanged code -> served verbatim, no LLM."""
    from megabrain.flows import serve_verbatim
    served = serve_verbatim(repo, _matched_flow(repo, 0.93))
    assert served is not None and "```" in served["text"]


def test_paraphrase_attaches_but_is_not_served(repo):
    """score in the attach band (0.62-0.88) must NOT serve — it narrates fresh
    with the flow as context."""
    from megabrain.flows import serve_verbatim
    assert serve_verbatim(repo, _matched_flow(repo, 0.70)) is None


def test_serve_refuses_when_code_changed(repo):
    """The sha recheck: even at serve-level similarity, a cited file that
    changed since caching means NO verbatim serve — never stale code."""
    from megabrain.flows import serve_verbatim
    flows = _matched_flow(repo, 0.93)
    (repo / "turn.py").write_text(TURN + "\n    x = 1\n")   # code moved on
    assert serve_verbatim(repo, flows) is None


def test_flow_files_are_pure_additions(repo):
    _cache(repo)
    st = load_state(repo)
    res = search_with_state(st, "how does the bot get interrupted on barge in")
    bundle = [t["file"] for t in res["tier1"]] + [t["file"] for t in res["tier2"]]
    assert len(bundle) == len(set(bundle)), "flow adds must not duplicate files"
    # every file the flow cites is somewhere in the bundle
    assert {"vad.py", "turn.py"} <= set(bundle)


def test_refresh_updates_instead_of_expiring(repo, monkeypatch):
    """A changed file: --refresh re-asks the flow's ORIGINAL question against the
    new code and re-caches it, rather than just dropping it."""
    from megabrain.flows import refresh_stale
    _cache(repo)
    (repo / "turn.py").write_text(TURN + "\n    state.log('changed')\n")
    index_repo(repo, quiet=True, prune_flows=False)  # update shas, keep the stale flow

    asked = []

    def fake_ask(root, q):
        asked.append(q)
        # a fresh synthesis over the current code (write path re-caches it)
        cache_flow(root, q, "## Updated barge-in flow [[0]] [[1]]",
                   ["vad.py", "turn.py"], emb=FakeEmbedder())
        return {"text": "## Updated barge-in flow"}

    from tests.conftest import FakeEmbedder
    rep = refresh_stale(repo, ask_fn=fake_ask)
    assert rep["refreshed"] == 1 and rep["dropped"] == 0
    assert asked == [FLOW_Q]                       # re-asked the ORIGINAL question
    with Store(repo) as s:
        metas, _, _ = s.load_flows()
    assert len(metas) == 1 and "Updated" in metas[0]["text"]


def test_sha_invalidation_on_reindex(repo):
    _cache(repo)
    (repo / "turn.py").write_text(TURN + "\n# changed\n")
    stats = index_repo(repo, quiet=True)
    assert stats["stale_flows_pruned"] == 1
    with Store(repo) as s:
        metas, _, _ = s.load_flows()
    assert metas == [], "a flow must die with the code it cites"


def test_near_duplicate_replaces(repo):
    _cache(repo)
    _cache(repo)                       # identical -> replaces, not accumulates
    with Store(repo) as s:
        metas, _, _ = s.load_flows()
    assert len(metas) == 1


def test_kill_switch_and_fail_open(repo, monkeypatch):
    monkeypatch.setenv("MEGABRAIN_FLOW_CACHE", "0")
    assert _cache(repo) is None
    monkeypatch.delenv("MEGABRAIN_FLOW_CACHE")
    # bogus root: swallowed, never raises (ask must not break on cache errors)
    from tests.conftest import FakeEmbedder
    assert cache_flow(repo / "nope", FLOW_Q, FLOW_TEXT, ["vad.py"],
                      emb=FakeEmbedder()) is None


def test_unrelated_query_attaches_no_flow(repo):
    _cache(repo)
    st = load_state(repo)
    res = search_with_state(st, "billing invoice multiplier constant")
    assert res["flows"] == []


def test_warm_flows_pre_caches_the_system(repo, monkeypatch):
    """Opt-in warmup: planner (LLM at index time) yields research questions;
    each ask's write path fills the cache — here both are injected fakes."""
    from megabrain.flows import warm_flows
    monkeypatch.setattr(
        "megabrain.providers.chat_text",
        lambda *a, **k: "how does barge in interrupt the bot end to end\n"
                        "how is billing computed for a call")
    from tests.conftest import FakeEmbedder

    def fake_ask(root, q):                   # a real ask would cache via its write path
        cache_flow(root, q, f"## Flow\nanswer about {q} citing code",
                   ["vad.py"], emb=FakeEmbedder())
        return {"text": "ok"}

    rep = warm_flows(repo, limit=2, ask_fn=fake_ask, quiet=True)
    assert rep["warmed"] == 2 and rep["flows_total"] >= 2
    assert all(w["cached"] for w in rep["questions"])


def test_warm_flows_respects_kill_switch(repo, monkeypatch):
    from megabrain.flows import warm_flows
    monkeypatch.setenv("MEGABRAIN_FLOW_CACHE", "0")
    rep = warm_flows(repo, limit=2, ask_fn=lambda r, q: {"text": "x"}, quiet=True)
    assert rep["warmed"] == 0 and rep["skipped"]


def test_mode_off_by_default_is_a_total_noop(tmp_path, fake_embedder):
    """The load-bearing requirement: a repo that never opted in behaves EXACTLY
    as before — no flow written, none loaded, no flows in the result."""
    (tmp_path / "vad.py").write_text(VAD)
    (tmp_path / "turn.py").write_text(TURN)
    index_repo(tmp_path, quiet=True)
    assert not flows_mod.enabled(tmp_path)          # off by default
    from tests.conftest import FakeEmbedder
    assert cache_flow(tmp_path, FLOW_Q, FLOW_TEXT, ["vad.py"],
                      emb=FakeEmbedder()) is None   # write path no-op
    st = load_state(tmp_path)
    assert st.flows == []                            # read path never loads
    res = search_with_state(st, "barge in interrupt")
    assert res["flows"] == []
