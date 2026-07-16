"""OpenRouter provider config + shared OpenAI-compatible clients.

One key — OPENROUTER_API_KEY (env or ~/.zshrc fallback) — for everything.
Both embeddings and chat go through OpenRouter's OpenAI-compatible API
(https://openrouter.ai/api/v1), so the whole engine is provider-agnostic:
any OpenRouter model works, selected purely by env. The defaults reproduce
the validated stack (pplx-embed-v1-0.6b embeddings — same 1024-dim int8
vectors as before) with qwen3-coder narrating `ask` (a code bakeoff
found it on par with claude-haiku-4.5 at ~5x lower cost — see evals/ASK_MODELS.md).

Env surface (all optional except an embedding credential):
    OPENROUTER_API_KEY      Bearer key for OpenRouter (chat + embeddings)
    OPENROUTER_BASE_URL     default https://openrouter.ai/api/v1
    MEGABRAIN_CHAT_PROVIDER 'claude' | 'openrouter' — route ask. Default
                            AUTO: claude when its SDK is importable, else
                            openrouter. Claude uses Claude Code subscription
                            credits or ANTHROPIC_API_KEY (providers_claude.py).
                            Embeddings ALWAYS use OpenRouter/local, never this.
    MEGABRAIN_EMBED_MODEL   default perplexity/pplx-embed-v1-0.6b
    MEGABRAIN_ASK_MODEL     default qwen/qwen3-coder ('haiku' on claude)
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

from ..errors import MegabrainError, MissingAPIKey, ProviderError


def _bad_request(msg: str) -> MegabrainError:
    e = MegabrainError(msg)
    e.http_status = 400
    return e

BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")

# Model defaults — provider/slug ids, overridable by env so any OpenRouter model works.
# ask defaults to qwen3-coder: a code bakeoff (evals/ASK_MODELS.md) found it matches
# claude-haiku-4.5 on citation selection (retrieval already guarantees completeness, so
# the LLM only narrates+points) at ~5x lower cost. Set MEGABRAIN_ASK_MODEL=anthropic/
# claude-haiku-4.5 for the last bit of secondary-citation completeness.
EMBED_MODEL = os.environ.get("MEGABRAIN_EMBED_MODEL", "perplexity/pplx-embed-v1-0.6b")
ASK_MODEL = os.environ.get("MEGABRAIN_ASK_MODEL", "qwen/qwen3-coder")


def chat_provider() -> str:
    """Name of the resolved chat backend (see resolve()) — kept as the stable
    string-shaped accessor for callers/tests that only need the name."""
    return resolve().name


def ask_model() -> str:
    """ask narrator model — per-provider default when MEGABRAIN_ASK_MODEL is
    unset: Claude alias 'haiku' on the claude provider, else
    google/gemini-3-flash-preview on OpenRouter (~2× faster than qwen3-coder on
    a walkthrough at comparable quality — measured; see docs/GUIDE.md). Set
    MEGABRAIN_ASK_MODEL=qwen/qwen3-coder for the cheapest/broadest-citation
    option, or any OpenRouter slug."""
    return os.environ.get("MEGABRAIN_ASK_MODEL") or \
        ("haiku" if chat_provider() == "claude" else "google/gemini-3-flash-preview")


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
        m = re.search(rf'^export {name}=["\']?([^"\'\s#]+)',
                      zshrc.read_text(encoding="utf-8", errors="replace"), re.M)
        if m:
            return m.group(1)
    if required:
        raise MissingAPIKey.named(name)
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
    """Key for the chat (ask) endpoint — same resolution as embeddings.
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
            raise ProviderError(f"openrouter {e.code}: {e.read()[:200]}",
                                status=e.code) from e
    raise ProviderError("unreachable")


def chat_text(model: str, prompt: str, max_tokens: int, temperature: float = 0.0,
              key: str | None = None, retries: int = 5, timeout: int = 45) -> str:
    """One non-streamed chat completion -> assistant text (OpenAI schema),
    routed through the resolved ChatProvider. `timeout` bounds BOTH backends
    (on claude it caps the CLI spawn)."""
    return resolve().chat_text(model, prompt, max_tokens, temperature=temperature,
                               key=key, retries=retries, timeout=timeout)


def stream_chat(body: dict, key: str | None = None, retries: int = 4,
                on_delta=None, timeout: int = 90, with_tools: bool = False):
    """Streamed chat completion, routed through the resolved ChatProvider.
    Returns (text, finish_reason) — or (text, finish_reason, tool_calls) when
    `with_tools=True` (ask v2 agents; the caller puts the `tools` spec in the
    body and runs the loop). finish_reason is OpenAI's ("length" on cap)."""
    return resolve().stream_chat(body, key=key, retries=retries,
                                 on_delta=on_delta, timeout=timeout,
                                 with_tools=with_tools)


def _or_stream_chat(body: dict, key: str | None = None, retries: int = 4,
                    on_delta=None, timeout: int = 90, with_tools: bool = False):
    """OpenAI-compatible SSE transport (OpenRouter/local/native endpoints).

    Parses `data: {...}` chunks: `choices[0].delta.content` deltas, terminated
    by `data: [DONE]`; SSE comment/keep-alive lines are skipped. Fragmented
    `delta.tool_calls` (id / function.name / function.arguments) accumulate per
    index into [{id, name, arguments}] in call order. Backoff on 429/5xx; once
    any delta has been emitted via on_delta we stop retrying so the terminal
    never double-prints."""
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
    raise last if last else ProviderError("unreachable")


# ── the provider registry (base.ChatProvider — the ChunkStrategy pattern) ──
# Adding a backend = one adapter class + one _REGISTRY entry. No call-site
# edits: chat_text/stream_chat above and ask_agents' tool loop all route
# through resolve(), and capabilities are probed (p.agent_stream), not
# name-switched.


class OpenRouterProvider:
    """Any OpenAI-compatible endpoint (OpenRouter, Ollama, LM Studio, vLLM,
    a provider's native API) — the always-available default."""

    name = "openrouter"
    agent_stream = None      # no native tool loop: callers run the OpenAI one

    def available(self) -> bool:
        return True

    def chat_text(self, model: str, prompt: str, max_tokens: int,
                  temperature: float = 0.0, key: str | None = None,
                  retries: int = 5, timeout: int = 45) -> str:
        body = {"model": model, "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}]}
        d = post_json("/chat/completions", body, key or find_chat_key(),
                      retries=retries, timeout=timeout, base_url=CHAT_BASE_URL)
        return (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""

    def stream_chat(self, body: dict, key: str | None = None, retries: int = 4,
                    on_delta=None, timeout: int = 90, with_tools: bool = False):
        return _or_stream_chat(body, key=key, retries=retries, on_delta=on_delta,
                               timeout=timeout, with_tools=with_tools)


class ClaudeProvider:
    """Claude Agent SDK (Claude Code subscription credits / ANTHROPIC_API_KEY).
    Self-gates on the SDK being importable; carries the native tool-loop
    capability (agent_stream) the OpenAI path lacks."""

    name = "claude"

    def available(self) -> bool:
        import importlib.util
        return importlib.util.find_spec("claude_agent_sdk") is not None

    def chat_text(self, model: str, prompt: str, max_tokens: int,
                  temperature: float = 0.0, key: str | None = None,
                  retries: int = 5, timeout: int = 45) -> str:
        from . import claude as _claude
        return _claude.chat_text(model, prompt, max_tokens, timeout=timeout)

    def stream_chat(self, body: dict, key: str | None = None, retries: int = 4,
                    on_delta=None, timeout: int = 90, with_tools: bool = False):
        from . import claude as _claude
        text, stop = _claude.stream_chat(body, on_delta=on_delta)
        # the SDK runs its own tool loop -> this route never returns tool_calls
        return (text, stop, []) if with_tools else (text, stop)

    @property
    def agent_stream(self):
        from . import claude as _claude
        return _claude.agent_stream


_REGISTRY: dict[str, object] = {"openrouter": OpenRouterProvider(),
                                "claude": ClaudeProvider()}
_PRIORITY = ("claude", "openrouter")


OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_OPENAI = OLLAMA_HOST + "/v1"
_select_lock = __import__("threading").Lock()


def _ollama_probe(timeout: float = 0.3) -> dict:
    """Ollama's local HTTP API — up? which models? is the binary installed?
    Never hangs (short budget: a settings panel must not block on a dead port)."""
    import shutil
    installed = bool(shutil.which("ollama"))
    try:
        with urllib.request.urlopen(OLLAMA_HOST + "/api/tags", timeout=timeout) as r:
            models = json.load(r).get("models") or []
            return {"up": True, "installed": True,
                    "models": [m["name"] for m in models]}
    except Exception:
        return {"up": False, "installed": installed, "models": []}


def _active_label() -> str:
    """The LOGICAL active chat provider for the UI (claude / openrouter /
    ollama). resolve() only knows the transport ('openrouter' covers any
    OpenAI-compatible endpoint), so a localhost CHAT_BASE_URL reads as
    ollama — the distinction the settings panel needs to highlight the card."""
    if _is_local(CHAT_BASE_URL):
        return "ollama"
    return resolve().name


def detect() -> dict:
    """What can narrate on this machine — the UI's settings/providers panel.
    Composes the existing detectors (Claude SDK importable · OPENROUTER_API_KEY)
    with an Ollama probe. `active` is what resolve()/ask_model() pick right now
    (plus the logical `label`)."""
    return {
        "claude": {"available": _REGISTRY["claude"].available(),
                   "default_model": "haiku"},
        "openrouter": {"available": bool(find_key(required=False)),
                       "default_model": "google/gemini-3-flash-preview"},
        "ollama": _ollama_probe(),
        "active": {"provider": resolve().name, "label": _active_label(),
                   "model": ask_model()},
    }


def select(provider: str, model: str | None = None) -> dict:
    """Point the engine's CHAT (ask/narration) role at a provider for every
    SUBSEQUENT call — the studio's provider switch. Single-user serve-api:
    set-and-leave under a lock (the reference webui pattern), never mutating
    embeddings. Ollama = the OpenAI-compatible transport aimed at :11434.
    Returns the fresh detect()."""
    global CHAT_BASE_URL
    p = (provider or "").strip().lower()
    with _select_lock:
        if p == "ollama":
            os.environ["MEGABRAIN_CHAT_PROVIDER"] = "openrouter"   # OpenAI-compat
            CHAT_BASE_URL = OLLAMA_OPENAI
            os.environ["MEGABRAIN_CHAT_BASE_URL"] = CHAT_BASE_URL
        elif p in ("openrouter", "claude"):
            os.environ["MEGABRAIN_CHAT_PROVIDER"] = p
            CHAT_BASE_URL = BASE_URL
            os.environ.pop("MEGABRAIN_CHAT_BASE_URL", None)
        else:
            raise _bad_request(f"unknown provider: {provider}")
        if model:
            os.environ["MEGABRAIN_ASK_MODEL"] = model
        else:
            os.environ.pop("MEGABRAIN_ASK_MODEL", None)
    return detect()


def start_ollama(wait_s: float = 6.0) -> dict:
    """Best-effort `ollama serve` when the binary is installed but the server
    is down — the studio's one-click start. Spawns detached, then polls until
    the API answers or the budget runs out. Returns the fresh detect()."""
    import shutil
    import subprocess
    import time as _t
    probe = _ollama_probe()
    if probe["up"]:
        return detect()
    if not shutil.which("ollama"):
        raise _bad_request("ollama is not installed — see https://ollama.com")
    try:
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception as e:  # noqa: BLE001
        raise ProviderError(f"could not start ollama: {e}") from e
    deadline = _t.time() + wait_s
    while _t.time() < deadline:
        if _ollama_probe(timeout=0.5)["up"]:
            break
        _t.sleep(0.4)
    return detect()


def resolve():
    """The ChatProvider for this call — read per call so tests/shells can flip
    it. MEGABRAIN_CHAT_PROVIDER pins by name (an unknown name falls back to
    openrouter, the always-available default — fail-open, exactly like the old
    switch). Unset = AUTO: the first provider whose available() self-gate
    passes, in _PRIORITY order (claude first, so a Claude Code user gets
    subscription-credit narration with zero config; embeddings are never
    affected by this)."""
    v = (os.environ.get("MEGABRAIN_CHAT_PROVIDER") or "").strip().lower()
    if v:
        return _REGISTRY.get(v, _REGISTRY["openrouter"])
    for name in _PRIORITY:
        if _REGISTRY[name].available():
            return _REGISTRY[name]
    return _REGISTRY["openrouter"]
