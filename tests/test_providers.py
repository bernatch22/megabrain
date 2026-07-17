"""providers.py unit tests: key resolution, local endpoints, retry, SSE parsing.
All offline — network is monkeypatched."""

import io
import json
import urllib.error
from pathlib import Path

import pytest

from megabrain import providers
from megabrain.errors import MegabrainError


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """No provider env vars, no real ~/.zshrc."""
    for v in ("OPENROUTER_API_KEY", "PERPLEXITY_API_KEY",
              "MEGABRAIN_EMBED_API_KEY", "MEGABRAIN_CHAT_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


# ---------------------------------------------------------------- keys

def test_find_key_env_wins(clean_env, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    assert providers.find_key() == "sk-env"


def test_find_key_zshrc_fallback(clean_env):
    (clean_env / ".zshrc").write_text('export OPENROUTER_API_KEY="sk-zshrc"  # c\n')
    assert providers.find_key() == "sk-zshrc"


def test_find_key_missing_raises_or_none(clean_env):
    with pytest.raises(RuntimeError):
        providers.find_key()
    assert providers.find_key(required=False) is None


def test_local_endpoints_need_no_key(clean_env):
    assert providers._key_for("http://localhost:11434/v1", None, True) == "local"
    assert providers._key_for("http://127.0.0.1:1234/v1", None, True) == "local"


def test_perplexity_native_uses_its_key(clean_env, monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-k")
    assert providers._key_for("https://api.perplexity.ai/v1", None, True) == "pplx-k"


def test_explicit_key_wins(clean_env):
    assert providers._key_for("https://api.perplexity.ai/v1", "explicit", True) == "explicit"


def test_is_local():
    assert providers._is_local("http://localhost:11434/v1")
    assert providers._is_local("http://host.docker.internal:11434/v1")
    assert not providers._is_local("https://openrouter.ai/api/v1")
    assert not providers._is_local("https://api.perplexity.ai/v1")


def test_attribution_headers_openrouter_only():
    h = providers._headers("k", "https://openrouter.ai/api/v1")
    assert "HTTP-Referer" in h and "X-Title" in h
    h = providers._headers("k", "http://localhost:11434/v1")
    assert "HTTP-Referer" not in h
    assert h["Authorization"] == "Bearer k"


# ---------------------------------------------------------------- HTTP mocks

class _Resp:
    def __init__(self, payload: bytes):
        self._p = payload

    def read(self):
        return self._p

    def __iter__(self):
        return iter(self._p.splitlines(keepends=True))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code: int):
    return urllib.error.HTTPError("http://x", code, "err", None, io.BytesIO(b"boom"))


def test_post_json_retries_on_429(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise _http_error(429)
        return _Resp(json.dumps({"ok": True}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    d = providers.post_json("/x", {"a": 1}, key="k")
    assert d == {"ok": True} and len(calls) == 2


def test_post_json_gives_up_on_4xx(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=0: (_ for _ in ()).throw(_http_error(400)))
    with pytest.raises(RuntimeError, match="400"):
        providers.post_json("/x", {}, key="k", retries=3)


def test_stream_chat_parses_sse(monkeypatch):
    events = [
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n',
        b': keep-alive comment\n',
        b'data: {"choices":[{"delta":{"content":" world"},"finish_reason":null}]}\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
        b'data: [DONE]\n',
        b'data: {"choices":[{"delta":{"content":"IGNORED"}}]}\n',
    ]
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=0: _Resp(b"".join(events)))
    deltas = []
    text, finish = providers.stream_chat({"model": "m", "messages": []},
                                         key="k", on_delta=deltas.append)
    assert text == "Hello world"
    assert finish == "stop"
    assert deltas == ["Hello", " world"]


def test_stream_chat_accumulates_fragmented_tool_calls(monkeypatch):
    """with_tools=True: delta.tool_calls fragments (id, name, argument pieces)
    accumulate per index; plain callers keep the 2-tuple contract untouched."""
    events = [
        b'data: {"choices":[{"delta":{"content":"Let me check."}}]}\n',
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
        b'"function":{"name":"search_more","arguments":""}}]}}]}\n',
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        b'"function":{"arguments":"{\\"que"}}]}}]}\n',
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        b'"function":{"arguments":"ry\\": \\"x\\"}"}}]}}]}\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n',
        b'data: [DONE]\n',
    ]
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=0: _Resp(b"".join(events)))
    text, finish, calls = providers.stream_chat(
        {"model": "m", "messages": [], "tools": []}, key="k", with_tools=True)
    assert text == "Let me check."
    assert finish == "tool_calls"
    assert calls == [{"id": "call_1", "name": "search_more",
                      "arguments": '{"query": "x"}'}]


def test_stream_chat_without_tools_keeps_two_tuple(monkeypatch):
    events = [b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}\n',
              b'data: [DONE]\n']
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=0: _Resp(b"".join(events)))
    out = providers.stream_chat({"model": "m", "messages": []}, key="k")
    assert out == ("hi", "stop")


def test_chat_text_extracts_message(monkeypatch):
    body = {"choices": [{"message": {"role": "assistant", "content": "OK"}}]}
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=0: _Resp(json.dumps(body).encode()))
    assert providers.chat_text("m", "hi", max_tokens=5, key="k") == "OK"


# ---------------------------------------------------------------- chat extras

@pytest.fixture
def sent(monkeypatch):
    """Capture the JSON body of the last request instead of sending it. The
    reply is plain JSON: post_json parses it, and stream_chat skips it as a
    non-`data:` line — so one fixture serves both transports."""
    box = {}

    def fake_urlopen(req, timeout=0):
        box.update(json.loads(req.data))
        return _Resp(json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return box


def test_chat_extra_merges_into_post_json(sent, monkeypatch):
    monkeypatch.setenv("MEGABRAIN_CHAT_EXTRA", '{"reasoning_effort": "none"}')
    providers.post_json("/chat/completions", {"model": "m", "messages": []}, key="k")
    assert sent["reasoning_effort"] == "none"
    assert sent["model"] == "m"


def test_chat_extra_merges_into_stream_chat(sent, monkeypatch):
    monkeypatch.setenv("MEGABRAIN_CHAT_EXTRA", '{"reasoning_effort": "none"}')
    providers.stream_chat({"model": "m", "messages": []}, key="k")
    assert sent["reasoning_effort"] == "none"
    assert sent["stream"] is True


def test_chat_extra_only_touches_chat_completions(sent, monkeypatch):
    """Embeddings share post_json — a chat-only knob must not leak into them
    (an unknown field is a 400 on strict providers)."""
    monkeypatch.setenv("MEGABRAIN_CHAT_EXTRA", '{"reasoning_effort": "none"}')
    providers.post_json("/embeddings", {"model": "e", "input": ["x"]}, key="k")
    assert "reasoning_effort" not in sent


def test_chat_extra_overrides_body(sent, monkeypatch):
    """Extras win: the point is forcing a knob the caller already set."""
    monkeypatch.setenv("MEGABRAIN_CHAT_EXTRA", '{"temperature": 0.9}')
    providers.stream_chat({"model": "m", "messages": [], "temperature": 0}, key="k")
    assert sent["temperature"] == 0.9


def test_chat_extra_absent_is_noop(sent, monkeypatch):
    monkeypatch.delenv("MEGABRAIN_CHAT_EXTRA", raising=False)
    providers.stream_chat({"model": "m", "messages": []}, key="k")
    assert set(sent) == {"model", "messages", "stream"}


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", '"a string"'])
def test_chat_extra_malformed_fails_loud(monkeypatch, raw):
    """A silently-dropped knob would corrupt every measurement that assumed it
    applied — better a 400 than a lie."""
    monkeypatch.setenv("MEGABRAIN_CHAT_EXTRA", raw)
    with pytest.raises(MegabrainError, match="MEGABRAIN_CHAT_EXTRA") as e:
        providers.stream_chat({"model": "m", "messages": []}, key="k")
    assert e.value.http_status == 400
