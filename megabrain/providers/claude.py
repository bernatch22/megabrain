"""Claude chat provider — `ask`/`--best` through the Claude Agent SDK.

Selected with MEGABRAIN_CHAT_PROVIDER=claude. The SDK drives the Claude Code
CLI, so it uses whatever credentials Claude Code already has:

- a logged-in Claude subscription (Claude Code credits — no API key at all), or
- ANTHROPIC_API_KEY in the env (bills the Anthropic API instead).

Requires the optional extra + the CLI:

    pip install 'megabrain[claude]'                 # claude-agent-sdk
    npm install -g @anthropic-ai/claude-code        # if not installed yet

Only CHAT goes through here. Embeddings keep using providers.py (OpenRouter or
a local OpenAI-compatible endpoint) — Anthropic has no embeddings API and
megabrain retrieval needs one.

The call is a pure narration turn: one turn, no tools, no project settings —
the Agent SDK is used strictly as a streamed chat transport.
"""

from __future__ import annotations

import asyncio


def _sdk():
    try:
        import claude_agent_sdk
        return claude_agent_sdk
    except ImportError as e:
        raise RuntimeError(
            "MEGABRAIN_CHAT_PROVIDER=claude requires the Claude Agent SDK: "
            "pip install 'megabrain[claude]' — plus the Claude Code CLI "
            "(logged in, or with ANTHROPIC_API_KEY set)") from e


def stream_chat(body: dict, on_delta=None) -> tuple[str, str]:
    """OpenAI-shaped chat body -> (text, finish_reason), streaming deltas to
    `on_delta` as they arrive (include_partial_messages). Mirrors the contract
    of providers.stream_chat so ask/rerank don't care which backend ran."""
    sdk = _sdk()
    prompt = "\n\n".join(m.get("content") or "" for m in body.get("messages", [])
                         if m.get("role") == "user")
    # The CLI is an AGENT runtime — without this belt-and-braces the model
    # sometimes tries to "search the codebase" instead of narrating, burning
    # the turn. Transport-level guard: declare no tools, deny the built-ins,
    # and say so in the prompt (the 2nd turn is margin for a denied attempt).
    prompt = ("You are running as a plain text generator: NO tools are "
              "available (no file reading, no search). Write the complete "
              "answer directly from the material below in ONE message.\n\n"
              + prompt)
    options = sdk.ClaudeAgentOptions(
        model=body.get("model") or "haiku",
        max_turns=2,
        allowed_tools=[],
        disallowed_tools=["Bash", "Read", "Write", "Edit", "Glob", "Grep",
                          "WebFetch", "WebSearch", "Task", "TodoWrite",
                          "NotebookEdit"],
        include_partial_messages=True,    # raw stream events -> live deltas
    )

    async def run() -> tuple[str, str]:
        parts: list[str] = []
        streamed = False
        stop = ""
        async for m in sdk.query(prompt=prompt, options=options):
            kind = type(m).__name__
            if kind == "StreamEvent":
                ev = getattr(m, "event", None) or {}
                if ev.get("type") == "content_block_delta":
                    t = (ev.get("delta") or {}).get("text") or ""
                    if t:
                        streamed = True
                        parts.append(t)
                        if on_delta is not None:
                            on_delta(t)
                elif ev.get("type") == "message_delta":
                    if (ev.get("delta") or {}).get("stop_reason") == "max_tokens":
                        stop = "length"
            elif kind == "AssistantMessage" and not streamed:
                # SDK builds without partial-message support: emit whole blocks
                for b in getattr(m, "content", []) or []:
                    t = getattr(b, "text", None)
                    if t:
                        parts.append(t)
                        if on_delta is not None:
                            on_delta(t)
        return "".join(parts), stop

    return asyncio.run(run())


def chat_text(model: str, prompt: str, max_tokens: int) -> str:
    """Non-streamed helper (rerank votes) — same transport, buffered."""
    text, _ = stream_chat({"model": model,
                           "messages": [{"role": "user", "content": prompt}]})
    return text
