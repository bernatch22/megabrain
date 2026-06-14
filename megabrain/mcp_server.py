"""Minimal MCP stdio server for megabrain (no external deps).

Tools:
  megabrain_ask(repo_path, question, docs?)   -> explained answer, real code spliced
                                                 (docs=true -> explain docs instead)
  megabrain_query(repo_path, task, compact?)  -> full unfiltered code bundle
  megabrain_get(repo_path, file, symbol?)     -> one file or symbol
  megabrain_index(repo_path)                  -> incremental index

Run: python3 -m megabrain.mcp_server
Register (claude code):
  claude mcp add megabrain -- python3 -m megabrain.mcp_server

See README.md for how retrieval + the ask explanation work.
"""

import json
import sys
from pathlib import Path

PROTOCOL = "2024-11-05"

TOOLS = [
    {
        "name": "megabrain_ask",
        "description": (
            "THE primary tool for any how/where/why question about an indexed repo. "
            "Returns a senior-engineer walkthrough that explains the whole relevant "
            "flow with the REAL code spliced in at each step (verbatim from disk, true "
            "line numbers — the model narrates and cites code spans but cannot rewrite "
            "them, so code is never hallucinated). Retrieval has no LLM; one Haiku call "
            "writes the explanation. Use this INSTEAD OF reading files one by one or "
            "spawning explore agents — one call replaces minutes of navigation. "
            "Non-cited related files are listed at the end. Explains CODE only by "
            "default; set docs=true to explain documentation (markdown) instead. ~6-19s."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "absolute path to the indexed repo root"},
                "question": {"type": "string", "description": "how/where/why question, natural language"},
                "docs": {"type": "boolean",
                         "description": "explain documentation (markdown) only, instead of code (default false)"},
            },
            "required": ["repo_path", "question"],
        },
    },
    {
        "name": "megabrain_query",
        "description": (
            "The same retrieval as megabrain_ask but UNFILTERED and with NO LLM "
            "(~200ms): returns ALL related code as a map — CORE (full code of the top "
            "files + symbol index) and RELATED (every connected file with its best "
            "chunk). Use when you want the raw complete bundle, when ask might have "
            "skipped something, or for speed."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "absolute path to the indexed repo root"},
                "task": {"type": "string", "description": "feature/question, natural language"},
                "compact": {"type": "boolean", "description": "signatures only, no code bodies"},
            },
            "required": ["repo_path", "task"],
        },
    },
    {
        "name": "megabrain_get",
        "description": "Fetch the full code of one file, or one symbol (e.g. Service.handle). Use to expand a RELATED entry or follow up after ask/query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string"},
                "file": {"type": "string", "description": "repo-relative path"},
                "symbol": {"type": "string", "description": "optional symbol name, e.g. Service.handle"},
            },
            "required": ["repo_path", "file"],
        },
    },
    {
        "name": "megabrain_index",
        "description": "Index or incrementally update a repo before querying a NEW one (fast: only changed files are re-embedded; ask/query auto-refresh a stale index).",
        "inputSchema": {
            "type": "object",
            "properties": {"repo_path": {"type": "string"}},
            "required": ["repo_path"],
        },
    },
]


AUTO_INDEX_TTL = 60  # seconds; refresh index before query if older than this


def _maybe_reindex(root: Path):
    import time

    from .store import Store
    meta = Store(root).get_meta("last_index")
    if not meta or time.time() - meta["t"] > AUTO_INDEX_TTL:
        from .indexer import index_repo
        index_repo(root, quiet=True)


def call_tool(name: str, args: dict) -> str:
    root = Path(args["repo_path"]).expanduser().resolve()
    if name == "megabrain_query":
        from .query import render, search
        _maybe_reindex(root)
        return render(search(root, args["task"]), compact=bool(args.get("compact")))
    if name == "megabrain_ask":
        from .ask import ask, render_ask
        _maybe_reindex(root)
        return render_ask(ask(root, args["question"], docs_only=bool(args.get("docs"))))
    if name == "megabrain_get":
        from .query import get_code
        return get_code(root, args["file"], args.get("symbol"))
    if name == "megabrain_index":
        from .indexer import index_repo
        return json.dumps(index_repo(root, quiet=True))
    raise ValueError(f"unknown tool {name}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid = msg.get("id")
        method = msg.get("method", "")
        if method == "initialize":
            result = {"protocolVersion": PROTOCOL,
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "megabrain", "version": "0.1.0"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = msg.get("params", {})
            try:
                text = call_tool(p.get("name", ""), p.get("arguments", {}))
                result = {"content": [{"type": "text", "text": text}]}
            except Exception as e:
                result = {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
        elif mid is None:
            continue  # notification
        else:
            result = {}
        if mid is not None:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
