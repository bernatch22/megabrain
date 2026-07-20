"""ask v2 — the confidence classifier, planner fail-open, repo map, and the
fan-out event flow with a fake chat provider (offline; no SDK, no network)."""

from types import SimpleNamespace

from megabrain.ask import agents as ask_agents
from megabrain.ask.narrator import _Splicer


def _t1(file, score):
    return {"file": file, "score": score, "chunks": [], "symbols": [], "neighbors": []}


def _t2(file, score, via=False):
    return {"file": file, "score": score, "via_graph": via, "matched": [],
            "doc": None, "best_chunk": None, "symbols": []}


# ── classifier ─────────────────────────────────────────────────────────────

def test_classify_scoped_single_core():
    res = {"tier1": [_t1("megabrain/ask.py", 1.2)],
           "tier2": [_t2("megabrain/providers/__init__.py", 0.9)]}
    out = ask_agents.classify_bundle(res, "how does ask work")
    assert not out["broad"] and out["reasons"] == []


def test_classify_broad_flat_tier1():
    res = {"tier1": [_t1(f"megabrain/f{i}.py", 1.2) for i in range(3)], "tier2": []}
    out = ask_agents.classify_bundle(res, "how does everything work")
    assert out["broad"] and out["reasons"]


def test_classify_broad_dir_spread():
    res = {"tier1": [_t1("a/x.py", 1.0), _t1("b/y.py", 0.99)],
           "tier2": [_t2("c/z.py", 0.8), _t2("d/w.py", 0.7)]}
    assert ask_agents.classify_bundle(res)["broad"]


def test_classify_broad_tier2_parity():
    res = {"tier1": [_t1("a/x.py", 1.0)],
           "tier2": [_t2(f"a/r{i}.py", 0.95) for i in range(4)]}
    assert ask_agents.classify_bundle(res)["broad"]


def test_classify_graph_extras_do_not_count_as_parity():
    res = {"tier1": [_t1("a/x.py", 1.0)],
           "tier2": [_t2(f"a/r{i}.py", 0.95, via=True) for i in range(6)]}
    assert not ask_agents.classify_bundle(res)["broad"]


def test_classify_issue_length_query():
    from itertools import product
    res = {"tier1": [_t1("a/x.py", 1.0)], "tier2": []}
    # >25 DISTINCT identifier tokens (the classifier tokenizes into a set)
    q = " ".join("token" + a + b for a, b in product("abcdef", repeat=2))
    assert ask_agents.classify_bundle(res, q)["broad"]


# ── repo map ───────────────────────────────────────────────────────────────

def test_repo_map_paths_and_doclines():
    st = SimpleNamespace(fpaths=[f"src/f{i}.py" for i in range(5)],
                         fskels=["one-line summary\nrest of skeleton"] * 5)
    m = ask_agents.repo_map(st)
    assert "src/f0.py — one-line summary" in m
    assert m.count("\n") == 4


def test_repo_map_caps_to_budget():
    st = SimpleNamespace(fpaths=[f"src/file_{i:03}.py" for i in range(50)],
                         fskels=["x" * 200] * 50)
    m = ask_agents.repo_map(st, max_chars=120)
    assert len(m) <= 150 and "more files" in m


# ── planner ────────────────────────────────────────────────────────────────

CANDS = [
    {"file": "megabrain/ask.py", "name": "ask", "kind": "function",
     "start_line": 1, "end_line": 10, "text": "def ask(): pass\n"},
    {"file": "megabrain/providers/__init__.py", "name": "stream_chat",
     "kind": "function", "start_line": 1, "end_line": 9,
     "text": "def stream_chat(): pass\n"},
    {"file": "tests/test_x.py", "name": "t", "kind": "function",
     "start_line": 1, "end_line": 5, "text": "def t(): pass\n"},
]


def test_plan_llm_validates_and_dedupes(monkeypatch):
    monkeypatch.setattr(
        ask_agents.providers, "chat_text",
        lambda *a, **k: '{"agents": ['
                        '{"label": "ask", "sub_query": "q1", "chunks": [0, 0]},'
                        '{"label": "prov", "sub_query": "q2", "chunks": [1, 99]}]}')
    plan = ask_agents._plan("q", CANDS, "map", key="k")
    assert [a["chunks"] for a in plan] == [[0], [1]]
    assert plan[0]["label"] == "ask" and plan[1]["sub_query"] == "q2"


def test_plan_llm_garbage_falls_back_to_dir_clustering(monkeypatch):
    monkeypatch.setattr(ask_agents.providers, "chat_text", lambda *a, **k: "not json")
    plan = ask_agents._plan("q", CANDS, "map", key="k")
    assert plan and len(plan) == 2          # megabrain/ vs tests/
    assigned = sorted(k for a in plan for k in a["chunks"])
    assert assigned == [0, 1, 2]            # clustering drops nothing


def test_plan_cluster_single_dir_is_none():
    cands = [dict(CANDS[0]), dict(CANDS[0])]
    assert ask_agents._plan_cluster("q", cands, 4) is None


def test_plan_cluster_folds_tail_into_last_slot():
    cands = [{**CANDS[0], "file": f"d{i}/x.py"} for i in range(6)]
    plan = ask_agents._plan_cluster("q", cands, 4)
    assert len(plan) == 4
    assert sorted(k for a in plan for k in a["chunks"]) == list(range(6))
    assert plan[-1]["label"].endswith("+misc")


# ── fan-out: events + global citations survive synthesis ──────────────────

def test_run_agents_events_and_global_citations(monkeypatch):
    events = []
    monkeypatch.setattr(ask_agents, "_plan", lambda *a, **k: [
        {"label": "ask", "sub_query": "q1", "chunks": [0]},
        {"label": "prov", "sub_query": "q2", "chunks": [1]}])
    monkeypatch.setattr(ask_agents, "repo_map", lambda st, max_chars=0: "MAP")

    def fake_agent_llm(prompt, tools, key, on_delta, max_tokens, model=None):
        k = 0 if "q1" in prompt else 1
        on_delta(f"part {k}\n")
        return f"Explains slice {k}.\n[[{k}]]\n"
    monkeypatch.setattr(ask_agents, "_agent_llm", fake_agent_llm)

    def fake_stream_chat(body, key=None, on_delta=None, **kw):
        text = "## Flow\nFirst part.\n[[0]]\nSecond part.\n[[1]]\n"
        assert "[[0]]" in body["messages"][0]["content"]   # partials reach the synth
        if on_delta:
            on_delta(text)
        return text, ""
    monkeypatch.setattr(ask_agents.providers, "stream_chat", fake_stream_chat)

    cands = CANDS[:2]
    spl = _Splicer(cands, {c["file"]: [] for c in cands})
    out = ask_agents.run_agents(".", "big question",
                                res={"repo": "demo", "tier1": [], "tier2": []},
                                cands=cands, st=None, key="k",
                                emit=events.append, splicer=spl)
    types = [e["type"] for e in events]
    assert types[0] == "planning" and types[1] == "plan"
    assert types.count("agent_start") == 2 and types.count("agent_done") == 2
    assert "synthesis_start" in types
    # global [[k]] citations survive the merge and splice to the REAL code
    assert "[[0]]" in out["text"] and "[[1]]" in out["text"]
    assert spl.cited == {0, 1}
    spliced = "".join(e["text"] for e in events if e["type"] == "synthesis_delta")
    assert "def ask(): pass" in spliced and "def stream_chat(): pass" in spliced
    assert [a["label"] for a in out["agents"]] == ["ask", "prov"]


def test_run_agents_survives_one_failed_agent(monkeypatch):
    events = []
    monkeypatch.setattr(ask_agents, "_plan", lambda *a, **k: [
        {"label": "ok", "sub_query": "q1", "chunks": [0]},
        {"label": "boom", "sub_query": "q2", "chunks": [1]}])
    monkeypatch.setattr(ask_agents, "repo_map", lambda st, max_chars=0: "MAP")

    def fake_agent_llm(prompt, tools, key, on_delta, max_tokens, model=None):
        if "q2" in prompt:
            raise RuntimeError("provider down")
        return "Only slice.\n[[0]]\n"
    monkeypatch.setattr(ask_agents, "_agent_llm", fake_agent_llm)

    out = ask_agents.run_agents(".", "q",
                                res={"repo": "demo", "tier1": [], "tier2": []},
                                cands=CANDS[:2], st=None, key="k",
                                emit=events.append)
    # single surviving partial IS the answer — no synthesis call needed
    assert out["text"] == "Only slice.\n[[0]]\n"
    assert any(e["type"] == "agent_error" for e in events)


def test_run_agents_raises_when_no_plan(monkeypatch):
    monkeypatch.setattr(ask_agents, "_plan", lambda *a, **k: None)
    monkeypatch.setattr(ask_agents, "repo_map", lambda st, max_chars=0: "MAP")
    import pytest
    with pytest.raises(RuntimeError, match="no fan-out plan"):
        ask_agents.run_agents(".", "q", res={"repo": "d", "tier1": [], "tier2": []},
                              cands=CANDS[:2], st=None, key="k")


# ── splicer: token-level streaming, partial citations never leak ───────────

def test_splicer_streams_prose_and_holds_partial_citations():
    spl = _Splicer(CANDS[:1], {"megabrain/ask.py": []})
    assert spl.feed("The flow starts ") == "The flow starts "   # no newline needed
    out = spl.feed("here:\n[[")
    assert "here:" in out and "[[" not in out                   # partial held back
    out = spl.feed("0]")
    assert out == ""                                            # still incomplete
    out = spl.feed("]\nend.")
    assert "def ask(): pass" in out and "end." in out           # spliced + resumed
    assert spl.cited == {0}
    assert spl.flush() == ""


def test_splicer_partial_range_citation_held_across_deltas():
    spl = _Splicer(CANDS[:1], {"megabrain/ask.py": []})
    assert "[[" not in spl.feed("see\n[[0:L1-")
    assert "def ask(): pass" in spl.feed("10]] done")
    # a lone bracket that turns out to be prose flushes on the next delta
    spl2 = _Splicer(CANDS[:1], {"megabrain/ask.py": []})
    assert spl2.feed("array[") == "array"
    assert spl2.feed("i]") == "[i]"


# ── stream_events: scoped question stays single-agent ──────────────────────

def test_stream_events_scoped_single_agent(monkeypatch):
    events = []
    st = SimpleNamespace(store=SimpleNamespace(symbols_for=lambda f: []),
                         fpaths=[], fskels=[])
    res = {"repo": "demo", "query": "q", "ms": 1, "tier2": [],
           "tier1": [{"file": "megabrain/ask.py", "score": 1.0, "symbols": [],
                      "neighbors": [],
                      "chunks": [{"id": 1, "name": "ask", "kind": "function",
                                  "part": None, "breadcrumb": None, "score": 1.0,
                                  "start_line": 1, "end_line": 10,
                                  "text": "def ask(): pass\n"}]}]}
    monkeypatch.setattr(ask_agents, "load_state", lambda *a, **k: st)
    monkeypatch.setattr(ask_agents, "search_with_state", lambda *a, **k: res)
    monkeypatch.setattr(ask_agents.providers, "find_chat_key",
                        lambda required=False: "k")
    monkeypatch.setattr(ask_agents.providers, "ask_model", lambda: "m")

    def fake_stream_chat(body, key=None, on_delta=None, **kw):
        t = "Here.\n[[0]]\n"
        if on_delta:
            on_delta(t)
        return t, ""
    monkeypatch.setattr(ask_agents.providers, "stream_chat", fake_stream_chat)

    out = ask_agents.stream_events(".", "how does ask work", events.append)
    types = [e["type"] for e in events]
    assert "classified" in types and "plan" not in types
    assert types[-1] == "done"
    assert out["text"] == "Here.\n[[0]]\n" and out["agents"] is None
    spliced = "".join(e["text"] for e in events if e["type"] == "synthesis_delta")
    assert "def ask(): pass" in spliced


def test_served_from_cache_still_ends_the_stream(monkeypatch):
    """`done` terminates EVERY path, including the serve-from-cache shortcut.
    A sink must not have to know which branch answered to know it ended."""
    events = []
    st = SimpleNamespace(store=SimpleNamespace(symbols_for=lambda f: []),
                         fpaths=[], fskels=[])
    res = {"repo": "demo", "query": "q", "ms": 1, "tier1": [], "tier2": [],
           "flows": [{"question": "cached q", "text": "prose\n```py\nx=1\n```\n",
                      "files": ["a.py"], "sha": {"a.py": "s"},
                      "score": 0.9, "qscore": 0.95}]}
    monkeypatch.setattr(ask_agents, "load_state", lambda *a, **k: st)
    monkeypatch.setattr(ask_agents, "search_with_state", lambda *a, **k: res)
    monkeypatch.setattr("megabrain.storage.flows.serve_verbatim",
                        lambda root, flows, question="": flows[0])

    out = ask_agents.stream_events(".", "q", events.append)
    types = [e["type"] for e in events]
    assert types == ["cached", "done"], types
    assert out["served_from_cache"] is True
    assert events[-1]["cached"] is True and events[-1]["llm_ms"] == 0


def test_uncited_answer_still_ends_the_stream(monkeypatch):
    """An answer that cites nothing falls open to the bundle — and must STILL
    emit `done`. Every sink treats that event as end-of-stream, so skipping it
    left the studio on "SYNTHESIS · STREAMING" forever, with no footer and no
    hint that the walkthrough was ungrounded."""
    events = []
    st = SimpleNamespace(store=SimpleNamespace(symbols_for=lambda f: []),
                         fpaths=[], fskels=[])
    res = {"repo": "demo", "query": "q", "ms": 1, "tier2": [],
           "tier1": [{"file": "megabrain/ask.py", "score": 1.0, "symbols": [],
                      "neighbors": [],
                      "chunks": [{"id": 1, "name": "ask", "kind": "function",
                                  "part": None, "breadcrumb": None, "score": 1.0,
                                  "start_line": 1, "end_line": 10,
                                  "text": "def ask(): pass\n"}]}]}
    monkeypatch.setattr(ask_agents, "load_state", lambda *a, **k: st)
    monkeypatch.setattr(ask_agents, "search_with_state", lambda *a, **k: res)
    monkeypatch.setattr(ask_agents.providers, "find_chat_key",
                        lambda required=False: "k")
    monkeypatch.setattr(ask_agents.providers, "ask_model", lambda: "m")

    def uncited(body, key=None, on_delta=None, **kw):
        # the exact failure: a header imitated from a cached flow, no [[k]]
        t = "**`megabrain/ask.py` L1-10** — ask\n"
        if on_delta:
            on_delta(t)
        return t, ""
    monkeypatch.setattr(ask_agents.providers, "stream_chat", uncited)

    ask_agents.stream_events(".", "how does ask work", events.append)
    types = [e["type"] for e in events]
    assert "bundle" in types, "ungrounded prose must fall open to the bundle"
    assert types[-1] == "done", types
    done = events[-1]
    assert done["grounded"] is False and done["spans"] == 0
