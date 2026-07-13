"""ChatProvider — the ONE contract every chat backend satisfies.

The same pattern as indexing.strategies.ChunkStrategy: N implementations bound
to one Protocol, resolved by a registry (providers.resolve()) instead of
if-switches at the call sites. Adding a backend = one adapter class + one
registry entry in providers/__init__.py — no call-site edits (OCP).

Capabilities are ATTRIBUTES, not subclass checks: `agent_stream` is None on
backends without a native tool loop, and callers probe it
(`if p.agent_stream: …`) — ask_agents falls back to the generic
OpenAI-function-calling loop it runs itself.

Embeddings are deliberately NOT part of this contract: retrieval always
embeds through providers.embeddings (OpenRouter/local), never the chat route.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class ChatProvider(Protocol):
    """One chat backend (OpenRouter-compatible endpoint, Claude Agent SDK, …).

    Contract notes:
    - `available()` is the SELF-GATE: cheap, no side effects (e.g. "is the SDK
      importable"). resolve() probes it in priority order.
    - `stream_chat` mirrors the OpenAI streaming contract: returns
      (text, finish_reason), or (text, finish_reason, tool_calls) when
      with_tools=True. Backends with a native tool loop return [] for calls.
    - `agent_stream` is the optional tool-enabled-turn capability:
      (prompt, model, tools, on_delta, timeout) -> str, where tools items are
      {name, description, schema, fn} and fn is a SYNC callable(args)->str.
      None = the caller runs its own function-calling loop over stream_chat.
    """

    name: str
    agent_stream: Callable[..., str] | None

    def available(self) -> bool: ...

    def chat_text(self, model: str, prompt: str, max_tokens: int,
                  temperature: float = 0.0, key: str | None = None,
                  retries: int = 5, timeout: int = 45) -> str: ...

    def stream_chat(self, body: dict, key: str | None = None, retries: int = 4,
                    on_delta: Callable[[str], None] | None = None,
                    timeout: int = 90, with_tools: bool = False) -> Any: ...
