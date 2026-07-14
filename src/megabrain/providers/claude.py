"""Claude chat provider — `ask` through the Claude Agent SDK.

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

Two transports:
- stream_chat / chat_text — the classic narration turn: one turn, no tools,
  the Agent SDK used strictly as a streamed chat transport.
- agent_stream — the ask v2 sub-agent turn: megabrain's retrieval tools are
  registered as an in-process MCP server and the SDK runs the tool loop
  itself. Builtins (Bash/Read/Grep/…) stay disallowed either way — the only
  world an agent can touch is megabrain retrieval, which has no LLM in it.
"""

from __future__ import annotations

import asyncio

# The CLI is an AGENT runtime — its builtins must stay off in BOTH transports:
# narration must not "search the codebase", and ask v2 sub-agents must reach
# code exclusively through megabrain's own retrieval tools.
_BUILTIN_TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep",
                  "WebFetch", "WebSearch", "Task", "TodoWrite",
                  "NotebookEdit"]


def _sdk():
    try:
        import claude_agent_sdk
        return claude_agent_sdk
    except ImportError as e:
        raise RuntimeError(
            "MEGABRAIN_CHAT_PROVIDER=claude requires the Claude Agent SDK: "
            "pip install 'megabrain[claude]' — plus the Claude Code CLI "
            "(logged in, or with ANTHROPIC_API_KEY set)") from e


def _consume(sdk, prompt: str, options, on_delta,
             timeout: float | None = None) -> tuple[str, str]:
    """Drain one sdk.query() run -> (text, finish_reason), streaming text
    deltas to on_delta (include_partial_messages) with a whole-block fallback
    for SDK builds without partial-message support. `timeout` (seconds) hard-
    bounds the run via asyncio.wait_for — the SDK spawns a full Claude Code
    CLI per call, which can stall in constrained environments; bounded callers
    (ask v2 planner/sub-agents) fail open instead of hanging."""
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
                for b in getattr(m, "content", []) or []:
                    t = getattr(b, "text", None)
                    if t:
                        parts.append(t)
                        if on_delta is not None:
                            on_delta(t)
        return "".join(parts), stop

    async def bounded() -> tuple[str, str]:
        if timeout:
            return await asyncio.wait_for(run(), timeout)
        return await run()

    return asyncio.run(bounded())


def stream_chat(body: dict, on_delta=None,
                timeout: float | None = None) -> tuple[str, str]:
    """OpenAI-shaped chat body -> (text, finish_reason), streaming deltas to
    `on_delta` as they arrive. Mirrors the contract of providers.stream_chat
    so ask doesn't care which backend ran. Pure narration: no tools."""
    sdk = _sdk()
    prompt = "\n\n".join(m.get("content") or "" for m in body.get("messages", [])
                         if m.get("role") == "user")
    # Without this belt-and-braces the model sometimes tries to "search the
    # codebase" instead of narrating, burning the turn. Transport-level guard:
    # declare no tools, deny the built-ins, and say so in the prompt (the 2nd
    # turn is margin for a denied attempt).
    prompt = ("You are running as a plain text generator: NO tools are "
              "available (no file reading, no search). Write the complete "
              "answer directly from the material below in ONE message.\n\n"
              + prompt)
    options = sdk.ClaudeAgentOptions(
        model=body.get("model") or "haiku",
        max_turns=2,
        allowed_tools=[],
        disallowed_tools=list(_BUILTIN_TOOLS),
        include_partial_messages=True,    # raw stream events -> live deltas
    )
    return _consume(sdk, prompt, options, on_delta, timeout=timeout)


def agent_stream(prompt: str, model: str | None, tools: list[dict],
                 on_delta=None, max_turns: int = 8,
                 timeout: float | None = None) -> str:
    """Tool-enabled agent turn (ask v2 sub-agents). `tools` items are
    {name, description, schema, fn} with fn a SYNC callable(args)->str —
    megabrain's no-LLM retrieval backends. They register as an in-process MCP
    server and the SDK runs the tool loop; builtins stay disallowed. Returns
    the concatenated assistant text across turns."""
    sdk = _sdk()
    handlers = []
    for t in tools:
        async def _handle(args, _fn=t["fn"]):
            out = await asyncio.to_thread(_fn, args or {})
            return {"content": [{"type": "text", "text": str(out)}]}
        handlers.append(sdk.tool(t["name"], t["description"], t["schema"])(_handle))
    server = sdk.create_sdk_mcp_server(name="megabrain", version="1.0.0",
                                       tools=handlers)
    options = sdk.ClaudeAgentOptions(
        model=model or "haiku",
        max_turns=max_turns,
        mcp_servers={"megabrain": server},
        allowed_tools=[f"mcp__megabrain__{t['name']}" for t in tools],
        disallowed_tools=list(_BUILTIN_TOOLS),
        include_partial_messages=True,
    )
    text, _stop = _consume(sdk, prompt, options, on_delta, timeout=timeout)
    return text


def chat_text(model: str, prompt: str, max_tokens: int,
              timeout: float | None = None) -> str:
    """Non-streamed helper (the ask v2 planner) — same transport,
    buffered; `timeout` bounds the CLI spawn (callers fail open)."""
    text, _ = stream_chat({"model": model,
                           "messages": [{"role": "user", "content": prompt}]},
                          timeout=timeout)
    return text
