"""OpenRouter provider config + shared OpenAI-compatible clients.

One key — OPENROUTER_API_KEY (env or ~/.zshrc fallback) — for everything.
Both embeddings and chat go through OpenRouter's OpenAI-compatible API
(https://openrouter.ai/api/v1), so the whole engine is provider-agnostic:
any OpenRouter model works, selected purely by env. The defaults reproduce
the validated stack (pplx-embed-v1-0.6b embeddings — same 1024-dim int8
vectors as before) with qwen3-coder narrating `ask`/`--best` (a code bakeoff
found it on par with claude-haiku-4.5 at ~5x lower cost — see evals/ASK_MODELS.md).

Env surface (all optional except the key):
    OPENROUTER_API_KEY      required — one Bearer key for chat + embeddings
    OPENROUTER_BASE_URL     default https://openrouter.ai/api/v1
    MEGABRAIN_EMBED_MODEL   default perplexity/pplx-embed-v1-0.6b
    MEGABRAIN_ASK_MODEL     default qwen/qwen3-coder
    MEGABRAIN_RERANK_MODEL  default qwen/qwen3-coder
    OPENROUTER_HTTP_REFERER / OPENROUTER_APP_TITLE  optional attribution headers

urllib only — no SDK dependency, matching the engine's no-framework stance.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")

# Model defaults — provider/slug ids, overridable by env so any OpenRouter model works.
# ask defaults to qwen3-coder: a code bakeoff (evals/ASK_MODELS.md) found it matches
# claude-haiku-4.5 on citation selection (retrieval already guarantees completeness, so
# the LLM only narrates+points) at ~5x lower cost. Set MEGABRAIN_ASK_MODEL=anthropic/
# claude-haiku-4.5 for the last bit of secondary-citation completeness.
EMBED_MODEL = os.environ.get("MEGABRAIN_EMBED_MODEL", "perplexity/pplx-embed-v1-0.6b")
ASK_MODEL = os.environ.get("MEGABRAIN_ASK_MODEL", "qwen/qwen3-coder")
RERANK_MODEL = os.environ.get("MEGABRAIN_RERANK_MODEL", "qwen/qwen3-coder")

# Optional OpenRouter attribution (leaderboard only — not required to function).
_REFERER = os.environ.get("OPENROUTER_HTTP_REFERER", "https://github.com/pinecall/megabrain")
_TITLE = os.environ.get("OPENROUTER_APP_TITLE", "megabrain")

_RETRY_CODES = (429, 500, 502, 503, 529)


def _resolve(name: str, required: bool = True) -> str | None:
    """An API key from env, else an `export NAME=...` line in ~/.zshrc."""
    v = os.environ.get(name)
    if v:
        return v
    zshrc = Path.home() / ".zshrc"
    if zshrc.exists():
        m = re.search(rf'^export {name}=["\']?([^"\'\s#]+)', zshrc.read_text(), re.M)
        if m:
            return m.group(1)
    if required:
        raise RuntimeError(f"{name} not set (env or ~/.zshrc)")
    return None


def find_key(required: bool = True) -> str | None:
    return _resolve("OPENROUTER_API_KEY", required)


# Embeddings can target a DIFFERENT OpenAI-compatible endpoint than chat — e.g.
# Perplexity's native API directly, to A/B a provider against OpenRouter:
#   MEGABRAIN_EMBED_BASE_URL=https://api.perplexity.ai/v1
#   MEGABRAIN_EMBED_MODEL=pplx-embed-v1-0.6b   (no provider prefix, direct)
# Default = OpenRouter (BASE_URL). Chat has no such override: Anthropic's native
# API is NOT OpenAI-shaped, so chat only makes sense through OpenRouter.
EMBED_BASE_URL = os.environ.get("MEGABRAIN_EMBED_BASE_URL", BASE_URL).rstrip("/")


def find_embed_key(required: bool = True) -> str | None:
    """Key for the embeddings endpoint: explicit MEGABRAIN_EMBED_API_KEY wins;
    else PERPLEXITY_API_KEY when pointed at Perplexity's native API; else the
    OpenRouter key."""
    if os.environ.get("MEGABRAIN_EMBED_API_KEY"):
        return os.environ["MEGABRAIN_EMBED_API_KEY"]
    if "perplexity.ai" in EMBED_BASE_URL:
        return _resolve("PERPLEXITY_API_KEY", required)
    return find_key(required)


def _headers(key: str, base_url: str = BASE_URL) -> dict:
    h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if "openrouter.ai" in base_url:          # attribution headers are OpenRouter-only
        h["HTTP-Referer"] = _REFERER
        h["X-Title"] = _TITLE
    return h


def post_json(path: str, body: dict, key: str | None = None, retries: int = 5,
              timeout: int = 120, base_url: str | None = None) -> dict:
    """POST a JSON body to `<base_url><path>`, return parsed JSON. Exponential
    backoff on 429/5xx. Used for embeddings and non-streamed chat."""
    base = (base_url or BASE_URL).rstrip("/")
    key = key or find_key()
    data = json.dumps(body).encode()
    for attempt in range(retries):
        req = urllib.request.Request(f"{base}{path}", data=data, method="POST",
                                     headers=_headers(key, base))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.loads(res.read())
        except urllib.error.HTTPError as e:
            if e.code in _RETRY_CODES and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"openrouter {e.code}: {e.read()[:200]}") from e
    raise RuntimeError("unreachable")


def chat_text(model: str, prompt: str, max_tokens: int, temperature: float = 0.0,
              key: str | None = None, retries: int = 5, timeout: int = 45) -> str:
    """One non-streamed chat completion -> assistant text (OpenAI schema)."""
    body = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}]}
    d = post_json("/chat/completions", body, key, retries=retries, timeout=timeout)
    return (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""


def stream_chat(body: dict, key: str | None = None, retries: int = 4,
                on_delta=None, timeout: int = 90) -> tuple[str, str]:
    """Streamed chat completion (OpenAI SSE). Returns (text, finish_reason).

    Parses `data: {...}` chunks: `choices[0].delta.content` deltas, terminated
    by `data: [DONE]`; SSE comment/keep-alive lines are skipped. Backoff on
    429/5xx; once any delta has been emitted via on_delta we stop retrying so
    the terminal never double-prints. finish_reason is OpenAI's ("length" on cap)."""
    key = key or find_key()
    body = {**body, "stream": True}
    data = json.dumps(body).encode()
    last: Exception | None = None
    emitted = False
    for attempt in range(retries):
        req = urllib.request.Request(f"{BASE_URL}/chat/completions", data=data,
                                     method="POST", headers=_headers(key))
        text, finish = "", ""
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                for raw in r:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload == "[DONE]":
                        break
                    try:
                        ev = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("error"):  # mid-stream provider error -> retry
                        raise urllib.error.HTTPError(req.full_url, 529,
                                                     "stream error", None, None)
                    ch = (ev.get("choices") or [{}])[0]
                    d = (ch.get("delta") or {}).get("content") or ""
                    if d:
                        text += d
                        if on_delta is not None:
                            on_delta(d)
                            emitted = True
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
            return text, finish
        except urllib.error.HTTPError as e:
            last = e
            if emitted:
                raise
            if e.code in _RETRY_CODES and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            if emitted:
                raise
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    raise last if last else RuntimeError("unreachable")
