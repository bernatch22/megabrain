"""llm_rerank: the LLM selects/reorders ids, the engine keeps its own chunks —
and EVERY failure mode falls open to the deterministic result."""

import json

import pytest

from megabrain.retrieval import rerank as rr


def _res(n=4, include_noise=True):
    res = {
        "query": "q", "repo": "r", "ms": 2,
        "chunks": [{"id": i, "file": f"src/f{i}.py", "start_line": 1,
                    "end_line": 9, "kind": "function", "name": f"fn{i}",
                    "score": 1.0 - i / 10,
                    "text": f'"""doc {i}"""\ndef fn{i}(): pass'}
                   for i in range(1, n + 1)],
        "kept": n, "pruned": 0, "scanned": n,
    }
    if include_noise:
        res["noise"] = []
    return res


@pytest.fixture
def chat(monkeypatch):
    """Monkeypatch providers.chat_text; returns a dict to set the reply."""
    box = {"reply": "[]", "calls": []}

    def fake_chat_text(model, prompt, max_tokens, **kw):
        box["calls"].append({"model": model, "prompt": prompt})
        if isinstance(box["reply"], Exception):
            raise box["reply"]
        return box["reply"]
    import megabrain.providers as providers
    monkeypatch.setattr(providers, "chat_text", fake_chat_text)
    return box


def test_reorders_and_drops(chat):
    chat["reply"] = "[3, 1]"
    res = rr.llm_rerank(_res(), "how does X work", model="m")
    assert [c["id"] for c in res["chunks"]] == [3, 1]
    assert res["kept"] == 2 and res["pruned"] == 2
    assert [c["id"] for c in res["noise"]] == [2, 4]      # dropped, not destroyed
    assert res["reranked"]["model"] == "m"
    assert res["reranked"]["kept"] == 2 and res["reranked"]["dropped"] == 2


def test_prose_around_the_array_is_tolerated(chat):
    chat["reply"] = "Sure! The relevant chunks are: [2, 4] — hope that helps."
    res = rr.llm_rerank(_res(), "q")
    assert [c["id"] for c in res["chunks"]] == [2, 4]


def test_unknown_ids_are_ignored(chat):
    chat["reply"] = "[99, 2, 77]"
    res = rr.llm_rerank(_res(), "q")
    assert [c["id"] for c in res["chunks"]] == [2]


def test_fail_open_on_garbage_reply(chat):
    chat["reply"] = "I cannot help with that."
    before = _res()
    res = rr.llm_rerank(before, "q")
    assert res["reranked"] is False
    assert [c["id"] for c in res["chunks"]] == [1, 2, 3, 4]   # untouched


def test_fail_open_on_exception(chat):
    chat["reply"] = TimeoutError("provider down")
    res = rr.llm_rerank(_res(), "q")
    assert res["reranked"] is False
    assert len(res["chunks"]) == 4


def test_fail_open_when_all_ids_unknown(chat):
    chat["reply"] = "[98, 99]"
    res = rr.llm_rerank(_res(), "q")
    assert res["reranked"] is False and len(res["chunks"]) == 4


def test_single_chunk_skips_the_call(chat):
    res = rr.llm_rerank(_res(1), "q")
    assert res["reranked"] is False
    assert chat["calls"] == []                             # no LLM spent on 1 chunk


def test_remote_lane_sends_full_bodies(chat):
    """Measured (6 queries x 4 views x 3 reps): the full-body view is the only
    one that both never missed the target AND ranked it #1 — partial evidence
    (a 6-line window) invited confident wrong exclusions, 12/18 kept. On a
    remote HTTP lane the judge gets the code."""
    chat["reply"] = "[1]"
    rr.llm_rerank(_res(), "how", model="m")
    prompt = chat["calls"][0]["prompt"]
    assert "def fn1(): pass" in prompt                     # bodies ARE the view
    assert "[1] src/f1.py:L1-9" in prompt


def test_batches_split_and_round_robin_merge(chat, monkeypatch):
    """>batch chunks on the bodies lane -> one judging call per slice, merged
    by position (every judge's #1 outranks any judge's #2)."""
    monkeypatch.setenv("MEGABRAIN_RERANK_BATCH", "4")
    import re as _re

    def per_batch_reply(model, prompt, max_tokens, **kw):
        chat["calls"].append({"model": model, "prompt": prompt})
        ids = _re.findall(r"^\[(\d+)\] src/", prompt, flags=_re.M)
        return json.dumps([int(i) for i in ids[:2]])       # keep first 2 of each
    import megabrain.providers as providers
    monkeypatch.setattr(providers, "chat_text", per_batch_reply)
    res = rr.llm_rerank(_res(10), "q", model="m")
    assert len(chat["calls"]) == 3                          # 4+4+2
    # batches keep [1,2],[5,6],[9,10] -> round-robin: 1,5,9,2,6,10
    assert [c["id"] for c in res["chunks"]] == [1, 5, 9, 2, 6, 10]
    assert res["reranked"]["batches"] == 3
    assert res["reranked"]["view"] == "code"


def test_one_failed_batch_fails_open_allowing_no_partial_result(chat, monkeypatch):
    """All-or-nothing: a partial merge would silently drop a whole batch's
    worth of candidates — worse than no rerank at all."""
    monkeypatch.setenv("MEGABRAIN_RERANK_BATCH", "4")
    calls = {"n": 0}

    def flaky(model, prompt, max_tokens, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise TimeoutError("batch 2 died")
        return "[1]"
    import megabrain.providers as providers
    monkeypatch.setattr(providers, "chat_text", flaky)
    res = rr.llm_rerank(_res(10), "q", model="m")
    assert res["reranked"] is False
    assert len(res["chunks"]) == 10                         # untouched


def test_local_endpoint_stays_on_the_compact_view(chat, monkeypatch):
    """A local server (Ollama) serializes and chokes on parallel ~9K-token
    prompts — the 1-line query-aware view, one call, is the local lane."""
    import megabrain.providers as providers
    monkeypatch.setattr(providers, "CHAT_BASE_URL", "http://localhost:11434/v1")
    chat["reply"] = "[1]"
    res = rr.llm_rerank(_res(10), "q", model="m")
    assert len(chat["calls"]) == 1
    assert "def fn1(): pass" not in chat["calls"][0]["prompt"]   # no bodies
    assert "doc 1" in chat["calls"][0]["prompt"]                 # hint view
    assert res["reranked"]["view"] == "hint"


def test_hint_prefers_the_line_sharing_query_identifiers(chat):
    """nx#35656 field case: a whole-file chunk led its card with an import
    line while the flag the question named (`analyzeSourceFiles`) sat at L36
    — and the judge, shown no evidence, dropped the one file that answered
    the question (and ranked it #3 on the runs it survived; #1 on 10/10 with
    the query-aware hint). The hint must surface the chunk line that shares
    identifiers with the question, falling back to the first line otherwise."""
    c = {"text": ("import { Foo } from './config';\n"
                  "export function build(cfg) {\n"
                  "  if (cfg.analyzeSourceFiles === true) { run(); }\n"
                  "}\n")}
    q = "Where is the analyzeSourceFiles option read and what does it gate?"
    assert "analyzeSourceFiles === true" in rr._hint(c, q)
    # no overlap -> first non-empty line, the pre-existing behavior
    assert rr._hint(c, "how is the rate limit refunded") == \
        "import { Foo } from './config';"
    assert rr._hint(c) == "import { Foo } from './config';"


def test_hint_weighs_identifier_length_not_count(chat):
    """One rare long name must outweigh several generic short ones — scoring
    by token COUNT would send the card to a line of boilerplate that shares
    `project`+`graph`+`read` over the line with the asked-about flag."""
    c = {"text": ("// read the project graph config for the graph read path\n"
                  "flags.analyzeSourceFiles = false;\n")}
    q = "where is analyzeSourceFiles read in the project graph?"
    assert "analyzeSourceFiles = false" in rr._hint(c, q)


def test_hint_reaches_the_prompt(chat):
    """The wiring, not just the helper: llm_rerank must pass the question
    into _hint so the listing card shows the query-relevant line."""
    chat["reply"] = "[1]"
    res = _res(2)
    res["chunks"][0]["text"] = ('import os\n'
                                'SPECIAL_TOGGLE = True  # gates the recompute\n')
    rr.llm_rerank(res, "what does SPECIAL_TOGGLE gate", model="m")
    assert "SPECIAL_TOGGLE" in chat["calls"][0]["prompt"]


def test_rerank_model_env_override(monkeypatch):
    monkeypatch.setenv("MEGABRAIN_RERANK_MODEL", "tiny/model")
    assert rr.rerank_model() == "tiny/model"


# ---------------------------------------------------- the fast lane (claude)

@pytest.fixture
def claude_provider(monkeypatch):
    """Simulate a Claude Code user: chat provider = claude, OpenRouter key on
    the machine. Records calls to BOTH lanes so a test can assert which ran."""
    import megabrain.providers as providers
    monkeypatch.setenv("MEGABRAIN_CHAT_PROVIDER", "claude")
    monkeypatch.setattr(providers, "find_key", lambda required=True: "or-key")
    lanes = {"provider": [], "openrouter": []}

    def provider_chat(model, prompt, max_tokens, **kw):
        lanes["provider"].append(model)
        return "[1]"

    def or_chat(model, prompt, max_tokens, **kw):
        lanes["openrouter"].append(model)
        return "[1]"
    monkeypatch.setattr(providers, "chat_text", provider_chat)
    monkeypatch.setattr(providers._REGISTRY["openrouter"], "chat_text", or_chat)
    return lanes


def test_claude_provider_reranks_on_the_fast_lane(claude_provider):
    """A rerank is a mechanical id filter: on the claude provider each
    chat_text spawns the Claude CLI (~18s measured from the MCP server, vs
    ~0.7s on the OpenAI-compat lane, identical selections) — so with an
    OpenRouter key available the rerank must take the fast lane."""
    import megabrain.providers as providers
    res = rr.llm_rerank(_res(), "q")
    assert res["reranked"] and res["reranked"]["model"] == providers.FAST_CHAT_MODEL
    assert claude_provider["openrouter"] == [providers.FAST_CHAT_MODEL]
    assert claude_provider["provider"] == []


def test_an_explicit_pin_keeps_provider_routing(claude_provider, monkeypatch):
    monkeypatch.setenv("MEGABRAIN_RERANK_MODEL", "haiku")
    res = rr.llm_rerank(_res(), "q")
    assert res["reranked"]["model"] == "haiku"
    assert claude_provider["provider"] == ["haiku"]
    assert claude_provider["openrouter"] == []


def test_no_key_and_no_local_endpoint_stays_on_the_provider(claude_provider,
                                                            monkeypatch):
    """Without a fast lane (no OpenRouter key, remote CHAT_BASE_URL) the claude
    provider remains the slow-but-working fallback — never a hard dependency."""
    import megabrain.providers as providers
    monkeypatch.setattr(providers, "find_key", lambda required=True: None)
    monkeypatch.setattr(providers, "CHAT_BASE_URL", "https://openrouter.ai/api/v1")
    res = rr.llm_rerank(_res(), "q")
    assert res["reranked"] is not False
    assert len(claude_provider["provider"]) == 1
    assert claude_provider["openrouter"] == []
    # and the CLI lane never carries bodies: each call spawns a ~18s process,
    # so it stays on the single compact-view call
    assert res["reranked"]["view"] == "hint"


# ------------------------------------------------- dropped tests are the spec

def _res_with_test(n=3):
    res = _res(n)
    res["chunks"].append({"id": 99, "file": "tests/test_f1.py", "start_line": 1,
                          "end_line": 40, "kind": "function",
                          "name": "test_fn1_behavior", "score": 0.95,
                          "text": "def test_fn1_behavior(): ..."})
    res["kept"] += 1
    return res


def test_dropped_test_files_surface_as_tests_not_noise(chat):
    """rails#57197 field case: the subsystem's test file WAS the spec (it
    pinned the instance-identity constraint that ruled out the issue author's
    fix), and the rerank dropped it invisibly. Dropped test chunks now land in
    res['tests'] — out of the signal list, never out of sight."""
    chat["reply"] = "[1, 2]"
    res = rr.llm_rerank(_res_with_test(), "how does fn1 work", model="m")
    assert [c["id"] for c in res["chunks"]] == [1, 2]
    assert [c["id"] for c in res["tests"]] == [99]
    assert all(c["id"] != 99 for c in res["noise"]), "test chunk lumped into noise"
    assert res["pruned"] == 1                       # the non-test drop only


def test_a_test_the_model_keeps_stays_in_the_signal_list(chat):
    chat["reply"] = "[99, 1]"
    res = rr.llm_rerank(_res_with_test(), "what pins fn1's contract", model="m")
    assert [c["id"] for c in res["chunks"]] == [99, 1]
    assert res["tests"] == []


def test_render_shows_the_tests_tail(chat):
    from megabrain.retrieval.render import render_pruned
    chat["reply"] = "[1]"
    res = rr.llm_rerank(_res_with_test(), "q", model="m")
    out = render_pruned(res, with_text=False)
    assert "tests pinning this behavior" in out
    assert "[99] tests/test_f1.py L1-40 · test_fn1_behavior" in out
