"""llm_rerank: the LLM selects/reorders ids, the engine keeps its own chunks —
and EVERY failure mode falls open to the deterministic result."""

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


def test_prompt_is_compact_no_bodies(chat):
    chat["reply"] = "[1]"
    rr.llm_rerank(_res(), "how", model="m")
    prompt = chat["calls"][0]["prompt"]
    assert "def fn1(): pass" not in prompt                 # bodies never sent
    assert "[1] src/f1.py:L1-9" in prompt                  # compact listing is
    assert "doc 1" in prompt                               # hint line included


def test_rerank_model_env_override(monkeypatch):
    monkeypatch.setenv("MEGABRAIN_RERANK_MODEL", "tiny/model")
    assert rr.rerank_model() == "tiny/model"
