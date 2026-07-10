"""Minimal MCP stdio server for megabrain (no external deps).

Tools:
  megabrain_ask(repo_path, question, scope_path?, docs?, include_docs?)
      -> explained answer, real code spliced (docs=true -> docs-only walkthrough;
         include_docs=true -> code + docs)
  megabrain_query(repo_path, task, scope_path?, compact?, full?)
      -> complete bundle: CORE full code + RELATED map (full=true adds RELATED code bodies)
  megabrain_get(repo_path, file, symbol?)     -> one file or symbol
  megabrain_chunks(repo_path, file, query)    -> every chunk of one file, scored + selected flags
  megabrain_index(repo_path)                  -> incremental index
  megabrain_forge(repo_path, ext?, list_only?, dry_run?)
      -> detect uncovered file types; LLM-generate + partition-validate + install
         a chunking strategy per type (repo-local, trust-gated)

Run: python3 -m megabrain.mcp_server
Register (claude code):
  claude mcp add megabrain -- python3 -m megabrain.mcp_server

See README.md for how retrieval + the ask explanation work.
"""

import json
import sys
from pathlib import Path

from .. import __version__

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
            "writes the explanation — and BROAD questions automatically fan out into "
            "parallel sub-agents (one per subsystem, with retrieval tools) whose "
            "answers are synthesized, same grounding. Use this INSTEAD OF reading "
            "files one by one or spawning explore agents — one call replaces minutes "
            "of navigation. Non-cited related files are listed at the end. Explains "
            "CODE only by default; set docs=true to explain documentation (markdown) "
            "instead. ~6-19s (broad fan-out: up to ~40s)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "path to the indexed repo root (a sub-path also works — the root is auto-detected from .megabrain)"},
                "question": {"type": "string", "description": "how/where/why question, natural language"},
                "scope_path": {"type": "string",
                               "description": "optional repo-relative folder (e.g. src/dispatch) to scope the walkthrough to files under it; omit for the whole repo"},
                "docs": {"type": "boolean",
                         "description": "explain documentation (markdown) only, instead of code (default false)"},
                "include_docs": {"type": "boolean",
                                 "description": "explain code AND docs together (default false = code only)"},
                "agents": {"type": "boolean",
                           "description": "true = force the multi-agent fan-out, false = never fan out; "
                                          "omit for AUTO (fan out only when the question is broad)"},
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
                "full": {"type": "boolean",
                         "description": "include RELATED best-chunk code bodies (default false: "
                                        "RELATED renders as a map — file, match span, symbols — "
                                        "so the bundle stays context-friendly)"},
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
        "name": "megabrain_chunks",
        "description": (
            "Score EVERY chunk of ONE file against a query: each chunk's span "
            "(start/end line), relevance score, and whether the full retrieval "
            "actually SELECTED it into the bundle. Shows signal-vs-noise inside a "
            "file (what retrieval reads vs ignores); powers chunk-selection "
            "visualizations."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "path to the indexed repo root (a sub-path also works — the root is auto-detected from .megabrain)"},
                "file": {"type": "string", "description": "repo-relative path of the file to map"},
                "query": {"type": "string", "description": "the retrieval query to score chunks against"},
            },
            "required": ["repo_path", "file", "query"],
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
    {
        "name": "megabrain_forge",
        "description": (
            "Make megabrain index file types it currently can't. Detects the repo's "
            "uncovered text extensions (e.g. .toml, .yaml, .astro, .proto), then for "
            "each one an LLM writes a chunking strategy from real sample files, which "
            "is only accepted after chunking EVERY matching file with a clean "
            "exact-line-partition (repair loop on failure, nothing unvetted is ever "
            "installed). The vetted strategy lands in .megabrain/strategies/ and is "
            "trusted, so every future index/auto-refresh loads it automatically. Use "
            "when queries miss content because its file type isn't indexed. "
            "list_only=true for the free census; dry_run=true to inspect the generated "
            "code without installing. ~10-60s per extension when generating."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "path to the repo root"},
                "ext": {"type": "string",
                        "description": "forge one extension only, e.g. '.toml'; omit to forge every detected candidate"},
                "list_only": {"type": "boolean",
                              "description": "just return the uncovered-extension census (no LLM call)"},
                "dry_run": {"type": "boolean",
                            "description": "generate + validate but do not install or reindex; the report includes the generated code"},
            },
            "required": ["repo_path"],
        },
    },
]


def _maybe_reindex(root: Path):
    from ..indexing.indexer import maybe_reindex
    maybe_reindex(root)


def _scope(args: dict) -> tuple[Path, str | None]:
    """Resolve repo_path (+ optional scope_path) to (repo_root, path_filter) for
    PATH-SCOPE. repo_path may itself be a sub-path inside an indexed repo; an
    explicit `scope_path` arg is appended to it. path_filter is None at the root."""
    from ..store import resolve_root
    p = Path(args["repo_path"]).expanduser()
    sub = (args.get("scope_path") or args.get("subpath") or "").strip().strip("/")
    if sub:
        p = p / sub
    root, subpath = resolve_root(p)
    return root, (subpath or None)


def call_tool(name: str, args: dict) -> str:
    if name == "megabrain_query":
        from ..retrieval.query import render, search
        root, pf = _scope(args)
        _maybe_reindex(root)
        return render(search(root, args["task"], path_filter=pf),
                      compact=bool(args.get("compact")),
                      related_code=bool(args.get("full")))
    if name == "megabrain_ask":
        from ..ask import ask, render_ask
        root, pf = _scope(args)
        _maybe_reindex(root)
        ag = args.get("agents")
        # MCP is request/response — the consuming agent only reads the final
        # text, so the fan-out runs buffered (no streaming) and the trace
        # lands as a one-line footer.
        out = ask(root, args["question"],
                  docs_only=bool(args.get("docs")),
                  include_docs=bool(args.get("include_docs")),
                  path_filter=pf,
                  agents=None if ag is None else bool(ag))
        text = render_ask(out)
        if out.get("agents"):
            tr = " · ".join(f'{a["label"]}({len(a["files"])}f)'
                            for a in out["agents"])
            text += f"\n\n— multi-agent: {tr}"
        return text
    if name == "megabrain_get":
        from ..retrieval.query import get_code
        from ..store import resolve_root
        root, sub = resolve_root(Path(args["repo_path"]).expanduser())
        rel = args["file"]
        if sub and not (root / rel).exists() and (root / sub / rel).exists():
            rel = (Path(sub) / rel).as_posix()
        return get_code(root, rel, args.get("symbol"))
    if name == "megabrain_chunks":
        from ..retrieval.query import chunks_for_file_root
        from ..store import resolve_root
        root, sub = resolve_root(Path(args["repo_path"]).expanduser())
        rel = args["file"]
        if sub and not (root / rel).exists() and (root / sub / rel).exists():
            rel = (Path(sub) / rel).as_posix()
        _maybe_reindex(root)
        return json.dumps(chunks_for_file_root(root, rel, args["query"]))
    if name == "megabrain_index":
        from ..indexing.indexer import index_repo
        root = Path(args["repo_path"]).expanduser().resolve()
        return json.dumps(index_repo(root, quiet=True))
    if name == "megabrain_forge":
        from ..forge import detect, forge, render_report
        root = Path(args["repo_path"]).expanduser().resolve()
        if args.get("list_only"):
            return json.dumps(detect(root), indent=1)
        report = forge(root, ext=args.get("ext"),
                       dry_run=bool(args.get("dry_run")), quiet=True)
        text = render_report(report)
        for e in report.get("forged", []):
            if e.get("code"):                # dry-run: show what would install
                text += f"\n\n--- generated {e['ext']} strategy ---\n{e['code']}"
        return text
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
                      "serverInfo": {"name": "megabrain", "version": __version__}}
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
