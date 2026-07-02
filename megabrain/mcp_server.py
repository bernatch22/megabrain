"""Minimal MCP stdio server for megabrain (no external deps).

Tools:
  megabrain_ask(repo_path, question, scope_path?, docs?)  -> explained answer, real code
                                                spliced (docs=true -> explain docs instead)
  megabrain_query(repo_path, task, scope_path?, compact?) -> full unfiltered code bundle
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
            "them, so code is never hallucinated). Retrieval has no LLM; one chat call "
            "writes the explanation. Use this INSTEAD OF reading files one by one or "
            "spawning explore agents — one call replaces minutes of navigation. "
            "Non-cited related files are listed at the end. Explains CODE only by "
            "default; set docs=true to explain documentation (markdown) instead. ~6-19s."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "path to the indexed repo root (a sub-path also works — the root is auto-detected from .megabrain)"},
                "question": {"type": "string", "description": "how/where/why question, natural language"},
                "scope_path": {"type": "string",
                               "description": "optional repo-relative folder (e.g. src/dispatch) to scope the walkthrough to files under it; omit for the whole repo"},
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
                "repo_path": {"type": "string", "description": "path to the indexed repo root (a sub-path also works — the root is auto-detected from .megabrain)"},
                "task": {"type": "string", "description": "feature/question, natural language"},
                "scope_path": {"type": "string",
                               "description": "optional repo-relative folder (e.g. src/dispatch) to scope the bundle to files under it; omit for the whole repo"},
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


def _scope(args: dict) -> tuple[Path, str | None]:
    """Resolve repo_path (+ optional scope_path) to (repo_root, path_filter) for
    PATH-SCOPE. repo_path may itself be a sub-path inside an indexed repo; an
    explicit `scope_path` arg is appended to it. path_filter is None at the root."""
    from .store import resolve_root
    p = Path(args["repo_path"]).expanduser()
    sub = (args.get("scope_path") or args.get("subpath") or "").strip().strip("/")
    if sub:
        p = p / sub
    root, subpath = resolve_root(p)
    return root, (subpath or None)


def call_tool(name: str, args: dict) -> str:
    if name == "megabrain_query":
        from .query import render, search
        root, pf = _scope(args)
        _maybe_reindex(root)
        return render(search(root, args["task"], path_filter=pf),
                      compact=bool(args.get("compact")))
    if name == "megabrain_ask":
        from .ask import ask, render_ask
        root, pf = _scope(args)
        _maybe_reindex(root)
        return render_ask(ask(root, args["question"],
                              docs_only=bool(args.get("docs")), path_filter=pf))
    if name == "megabrain_get":
        from .query import get_code
        from .store import resolve_root
        root, sub = resolve_root(Path(args["repo_path"]).expanduser())
        rel = args["file"]
        if sub and not (root / rel).exists() and (root / sub / rel).exists():
            rel = (Path(sub) / rel).as_posix()
        return get_code(root, rel, args.get("symbol"))
    if name == "megabrain_index":
        from .indexer import index_repo
        root = Path(args["repo_path"]).expanduser().resolve()
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
