"""OpenRouter provider config + shared OpenAI-compatible clients.

One key — OPENROUTER_API_KEY (env or ~/.zshrc fallback) — for everything.
Both embeddings and chat go through OpenRouter's OpenAI-compatible API
(https://openrouter.ai/api/v1), so the whole engine is provider-agnostic:
any OpenRouter model works, selected purely by env. The defaults reproduce
the validated stack (pplx-embed-v1-0.6b embeddings — same 1024-dim int8
vectors as before) with qwen3-coder narrating `ask`/`--best` (a code bakeoff
found it on par with claude-haiku-4.5 at ~5x lower cost — see evals/ASK_MODELS.md).

Env surface (all optional except an embedding credential):
    OPENROUTER_API_KEY      Bearer key for OpenRouter (chat + embeddings)
    OPENROUTER_BASE_URL     default https://openrouter.ai/api/v1
    MEGABRAIN_CHAT_PROVIDER 'claude' | 'openrouter' — route ask/--best. Default
                            AUTO: claude when its SDK is importable, else
                            openrouter. Claude uses Claude Code subscription
                            credits or ANTHROPIC_API_KEY (providers_claude.py).
                            Embeddings ALWAYS use OpenRouter/local, never this.
    MEGABRAIN_EMBED_MODEL   default perplexity/pplx-embed-v1-0.6b
    MEGABRAIN_ASK_MODEL     default qwen/qwen3-coder ('haiku' on claude)
    MEGABRAIN_RERANK_MODEL  default qwen/qwen3-coder ('haiku' on claude)
    OPENROUTER_HTTP_REFERER / OPENROUTER_APP_TITLE  optional attribution headers

Local / hybrid stacks — point embeddings and/or chat at ANY OpenAI-compatible
endpoint (Ollama, LM Studio, vLLM, or a provider's native API), independently
per role. localhost endpoints need no API key:
    MEGABRAIN_EMBED_BASE_URL / MEGABRAIN_EMBED_API_KEY
    MEGABRAIN_CHAT_BASE_URL  / MEGABRAIN_CHAT_API_KEY
  · fully local (Ollama):  EMBED+CHAT_BASE_URL=http://localhost:11434/v1,
      MEGABRAIN_EMBED_MODEL=embeddinggemma, MEGABRAIN_ASK_MODEL=qwen3-coder:30b
  · hybrid: local embed + OpenRouter chat (set only MEGABRAIN_EMBED_BASE_URL).

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


def chat_provider() -> str:
    """Chat backend for ask/--best (read per call so tests/shells can flip it):
    'claude' (Claude Agent SDK: Claude Code subscription credits, or
    ANTHROPIC_API_KEY) or 'openrouter' (any OpenAI-compatible endpoint, see
    CHAT_BASE_URL). Embeddings are NOT affected by this switch.

    Default is AUTO: Claude when its SDK is importable (so a Claude Code user
    gets subscription-credit narration with zero config), else OpenRouter (so a
    plain `pip install megabrain` still works out of the box). Set
    MEGABRAIN_CHAT_PROVIDER to pin it either way."""
    v = os.environ.get("MEGABRAIN_CHAT_PROVIDER")
    if v:
        return v.strip().lower()
    import importlib.util
    return "claude" if importlib.util.find_spec("claude_agent_sdk") else "openrouter"


def ask_model() -> str:
    """ask narrator model — per-provider default when MEGABRAIN_ASK_MODEL is
    unset: Claude alias 'haiku' on the claude provider, qwen3-coder on
    OpenRouter (the validated bakeoff pick)."""
    return os.environ.get("MEGABRAIN_ASK_MODEL") or \
        ("haiku" if chat_provider() == "claude" else "qwen/qwen3-coder")


def rerank_model() -> str:
    return os.environ.get("MEGABRAIN_RERANK_MODEL") or \
        ("haiku" if chat_provider() == "claude" else "qwen/qwen3-coder")

# Optional OpenRouter attribution (leaderboard only — not required to function).
_REFERER = os.environ.get("OPENROUTER_HTTP_REFERER", "https://github.com/bernatch22/megabrain")
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


# Embeddings and chat can each target a DIFFERENT OpenAI-compatible endpoint than
# OpenRouter — a provider's native API (e.g. api.perplexity.ai) or a LOCAL server
# (Ollama/LM Studio/vLLM). Both default to OpenRouter (BASE_URL).
EMBED_BASE_URL = os.environ.get("MEGABRAIN_EMBED_BASE_URL", BASE_URL).rstrip("/")
CHAT_BASE_URL = os.environ.get("MEGABRAIN_CHAT_BASE_URL", BASE_URL).rstrip("/")


def _is_local(url: str) -> bool:
    """A localhost / private-host endpoint (Ollama, LM Studio) — needs no auth."""
    return bool(re.search(r"://(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|"
                          r"host\.docker\.internal)\b", url))


def _key_for(base_url: str, explicit: str | None, required: bool) -> str | None:
    if _is_local(base_url):
        return explicit or "local"          # local servers ignore the auth header
    if explicit:
        return explicit
    if "perplexity.ai" in base_url:
        return _resolve("PERPLEXITY_API_KEY", required)
    return find_key(required)


def find_embed_key(required: bool = True) -> str | None:
    """Key for the embeddings endpoint (local → none; native provider → its key;
    else the OpenRouter key)."""
    return _key_for(EMBED_BASE_URL, os.environ.get("MEGABRAIN_EMBED_API_KEY"), required)


def find_chat_key(required: bool = True) -> str | None:
    """Key for the chat (ask/rerank) endpoint — same resolution as embeddings.
    On the claude provider there may be no key at all (subscription auth lives
    inside the Claude Code CLI), so a sentinel keeps ask's `if key` gate open."""
    if chat_provider() == "claude":
        return "claude"
    return _key_for(CHAT_BASE_URL, os.environ.get("MEGABRAIN_CHAT_API_KEY"), required)


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
    """One non-streamed chat completion -> assistant text (OpenAI schema).
    `timeout` bounds BOTH providers (on claude it caps the CLI spawn)."""
    if chat_provider() == "claude":
        from . import claude as providers_claude
        return providers_claude.chat_text(model, prompt, max_tokens,
                                          timeout=timeout)
    body = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}]}
    d = post_json("/chat/completions", body, key or find_chat_key(),
                  retries=retries, timeout=timeout, base_url=CHAT_BASE_URL)
    return (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""


def stream_chat(body: dict, key: str | None = None, retries: int = 4,
                on_delta=None, timeout: int = 90, with_tools: bool = False):
    """Streamed chat completion (OpenAI SSE). Returns (text, finish_reason) —
    or (text, finish_reason, tool_calls) when `with_tools=True` (ask v2 agents;
    the caller puts the `tools` spec in the body and runs the loop).

    Parses `data: {...}` chunks: `choices[0].delta.content` deltas, terminated
    by `data: [DONE]`; SSE comment/keep-alive lines are skipped. Fragmented
    `delta.tool_calls` (id / function.name / function.arguments) accumulate per
    index into [{id, name, arguments}] in call order. Backoff on 429/5xx; once
    any delta has been emitted via on_delta we stop retrying so the terminal
    never double-prints. finish_reason is OpenAI's ("length" on cap).

    MEGABRAIN_CHAT_PROVIDER=claude reroutes the same body through the Claude
    Agent SDK (Claude Code credits / ANTHROPIC_API_KEY) — same return contract.
    (Tool-enabled claude turns go through claude.agent_stream instead: the SDK
    runs its own tool loop, so the claude route never returns tool_calls.)"""
    if chat_provider() == "claude":
        from . import claude as providers_claude
        text, stop = providers_claude.stream_chat(body, on_delta=on_delta)
        return (text, stop, []) if with_tools else (text, stop)
    key = key or find_chat_key()
    body = {**body, "stream": True}
    data = json.dumps(body).encode()
    last: Exception | None = None
    emitted = False
    for attempt in range(retries):
        req = urllib.request.Request(f"{CHAT_BASE_URL}/chat/completions", data=data,
                                     method="POST", headers=_headers(key, CHAT_BASE_URL))
        text, finish = "", ""
        calls: dict[int, dict] = {}
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
                    delta = ch.get("delta") or {}
                    d = delta.get("content") or ""
                    if d:
                        text += d
                        if on_delta is not None:
                            on_delta(d)
                            emitted = True
                    for tc in delta.get("tool_calls") or []:
                        cur = calls.setdefault(tc.get("index", 0),
                                               {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            cur["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            cur["name"] = fn["name"]
                        if fn.get("arguments"):
                            cur["arguments"] += fn["arguments"]
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
            if with_tools:
                return text, finish, [calls[i] for i in sorted(calls)]
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
