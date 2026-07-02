"""providers.py unit tests: key resolution, local endpoints, retry, SSE parsing.
All offline — network is monkeypatched."""

import io
import json
import urllib.error
from pathlib import Path

import pytest

from megabrain import providers


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


def test_chat_text_extracts_message(monkeypatch):
    body = {"choices": [{"message": {"role": "assistant", "content": "OK"}}]}
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=0: _Resp(json.dumps(body).encode()))
    assert providers.chat_text("m", "hi", max_tokens=5, key="k") == "OK"
