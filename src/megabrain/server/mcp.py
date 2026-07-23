"""Minimal MCP stdio server for megabrain (no external deps).

Tools (deliberately few — every tool costs the calling agent context and a
decision; the host already has Read/Grep for single files, so megabrain only
exposes what it alone can do):
  megabrain_ask(repo_path, question, scope_path?, docs?)
      -> explained answer, real code spliced (docs=true -> docs-only walkthrough)
  megabrain_search(repo_path, task, scope_path?, compact?, rerank?, docs?)
      -> flat relevance-ranked signal chunks with the real code, noise dropped
         (megabrain_query is a deprecated dispatch alias for it)
  megabrain_graph(repo_path, mode?, node?, source?, target?, scope_path?)
      -> the repo as a knowledge graph: communities map / one node / a path
  megabrain_index(repo_path)                  -> incremental index
  megabrain_forge(repo_path, ext?, list_only?, dry_run?, specialize?)
      -> COVERAGE: detect uncovered file types; LLM-generate + partition-validate
         + install a chunker per type (repo-local, trust-gated). specialize=true
         only lists poorly-chunked covered files (LLM specialization was removed;
         hand-write + gate via megabrain.forge.specialize.gate_strategy)
  megabrain_flows(repo_path, action?, n?)   -> manage the flow cache (on by default)
      -> action list|warm|refresh|enable|disable (warm pre-caches N workflows)

Run: python3 -m megabrain.mcp_server
Register (claude code):
  claude mcp add megabrain -- python3 -m megabrain.mcp_server

See README.md for how retrieval + the ask explanation work.
"""

import json
import sys
from pathlib import Path

from .. import __version__
from ..errors import MegabrainError

PROTOCOL = "2024-11-05"

# Server-level instructions, returned by `initialize` and injected into the
# calling agent's context ONCE. This is the only megabrain text an agent is
# guaranteed to see: with tool search enabled (Claude Code's default) the tool
# SCHEMAS stay deferred and only names are visible until the agent searches for
# them — so what belongs here is the mental model and the routing between
# tools, not a restatement of each tool's own description.
#
# Every line below is a lesson from a real session, not documentation for its
# own sake; keep it that way, and keep it short — it costs context in every
# session that loads this server.
INSTRUCTIONS = """megabrain answers questions about a repo's CODE from a pre-built index, so you don't have to crawl files to understand it. Retrieval runs NO LLM: it ranks real chunks and returns verbatim code with true line numbers.

Reach for it FIRST on any how/where/why question about an indexed repo — one call usually replaces several rounds of Grep + Read. For exact literal strings use megabrain_grep.

Which tool:
- megabrain_grep — you know the exact identifier/string: every match resolved against the index and grouped into DEFINES / READS (ranked by dependents, with who-reaches-it edges) / CONFIG / TESTS / DOCS. One call answers "where is this defined, who reads it, who depends on the reader". Zero LLM, ~50ms.
- megabrain_search — you want the exact code to read: ranked chunks with the real code, noise dropped, plus a cheap LLM rerank. The default for a reproducible bug — when two spans collide, seeing them side by side IS the explanation.
- megabrain_ask — you want the flow narrated across subsystems with code spliced in at each step (broad questions fan out into sub-agents). The spliced CODE is verbatim and cannot be hallucinated; the PROSE around it is model narration, so verify its claims against that code before acting on them.
- megabrain_graph — the repo as a map: communities, core abstractions, how two areas connect. Start an unfamiliar codebase here.
- megabrain_index — register/refresh a repo (ask/search already auto-refresh a stale index).
- megabrain_flows — walkthroughs cached from previous asks.
- megabrain_forge — add a chunker for a file type megabrain doesn't cover yet.

TRUST the CODE, verify the PROSE. Spliced code is verbatim from disk with true line numbers — never re-verify it with grep or re-Read files whose code the render included. But ask's prose is narration: check its behavior claims (who calls what, when) against the code before hooking into anything (one narration called a summary path "failure-only"; it also serves -rP). ONE scoped search, then work from it.

Two things that decide answer quality:
- scope_path EXCLUDES everything outside it from retrieval. Scope to a package root (e.g. activejob), never to its lib/ or src/ subfolder — that cuts away the package's tests, which are often the spec of the behavior you are asking about.
- On a bug, name the STATE to track, not just the symptom: "where along this path could scheduled_at be lost?" returns a trace; "why does the retry fire immediately?" invites a theory."""

TOOLS = [
    {
        "name": "megabrain_ask",
        "description": (
            "THE primary tool for any how/where/why question about an indexed repo. "
            "Returns a senior-engineer walkthrough that explains the whole relevant "
            "flow with the REAL code spliced in at each step (verbatim from disk, true "
            "line numbers — the model narrates and cites code spans but cannot rewrite "
            "them, so the CODE is never hallucinated; the prose around it is LLM "
            "narration, so verify its claims against the spliced code before relying "
            "on them, especially for bug/root-cause questions). Retrieval has no LLM; "
            "one chat call "
            "writes the explanation — and BROAD questions automatically fan out into "
            "parallel sub-agents (one per subsystem, with retrieval tools) whose "
            "answers are synthesized, same grounding. Use this INSTEAD OF reading "
            "files one by one or spawning explore agents — one call replaces minutes "
            "of navigation. Non-cited related files are listed at the end. Explains "
            "CODE only by default; set docs=true to explain documentation (markdown) "
            "instead. ~6-19s (broad fan-out: up to ~40s). Budget your discovery: "
            "ONE ask covers a flow — do not chain asks per sub-question; after it, "
            "Read only the files you will edit."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "path to the indexed repo root (a sub-path also works — the root is auto-detected from .megabrain)"},
                "question": {"type": "string", "description": "how/where/why question, natural language"},
                "scope_path": {"type": "string",
                               "description": "optional repo-relative folder to scope the walkthrough to files under it; omit for the whole repo. Scoping EXCLUDES everything outside the folder from retrieval entirely — scope to the package/subsystem root (e.g. activejob, src/dispatch), never to its lib/ or src/ subfolder, or you cut away the package's tests, which are often the spec of the behavior you are asking about"},
                "docs": {"type": "boolean",
                         "description": "explain documentation (markdown) only, instead of code (default false)"},
                "agents": {"type": "boolean",
                           "description": "true = force the multi-agent fan-out, false = never fan out; "
                                          "omit for AUTO (fan out only when the question is broad)"},
                "page": {"type": "integer",
                         "description": "long walkthroughs paginate at code-block boundaries "
                                        "instead of overflowing the host limit; when the footer "
                                        "says there are more pages, call again with the SAME "
                                        "question and page=2 (flow-cached, ~0ms). Read every "
                                        "page before acting — partial evidence ships bugs."},
            },
            "required": ["repo_path", "question"],
        },
    },
    {
        "name": "megabrain_search",
        "description": (
            "The same retrieval as megabrain_ask but with NO LLM (~200ms): a flat, "
            "relevance-ranked list of exactly the chunks worth reading "
            "([id] file:lines · score + CODE), with the noise dropped. EVERY related "
            "file still appears, each with its best-matching chunk — so nothing "
            "relevant is missed at the FILE level. Chunks are filtered though: a "
            "file's other chunks are cut, so when you need one file in full, Read it "
            "(the path and line numbers are right there). Test files the rerank "
            "keeps out of the signal list are appended as a compact 'tests pinning "
            "this behavior' section — they are the spec of the mechanism; read them "
            "before changing it. One call hands you the real code, no follow-up "
            "fetch needed. Use it when you want the exact code to read rather than "
            "a narration — deterministic, no LLM. TOKEN RULE for implement/edit "
            "tasks: full bodies are for files you will NOT edit. The host requires "
            "Read before Edit, so a full-body render of an edit target gets paid "
            "TWICE — call with compact=true for a span map, then Read each edit "
            "target once. One boundary to know: retrieval "
            "ranks what EXISTS — when the bug is a MISSING call/flag/parameter, "
            "search shows you the site to inspect but cannot flag the absence. To "
            "PROVE an absence, megabrain_grep the identifier: zero matches over "
            "the indexed corpus, grouped by role, is the evidence."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "path to the indexed repo root (a sub-path also works — the root is auto-detected from .megabrain)"},
                "task": {"type": "string", "description": "feature/question, natural language"},
                "scope_path": {"type": "string",
                               "description": "optional repo-relative folder to scope the bundle to files under it; omit for the whole repo. Scoping EXCLUDES everything outside the folder from retrieval entirely — scope to the package/subsystem root (e.g. activejob, src/dispatch), never to its lib/ or src/ subfolder, or you cut away the package's tests, which are often the spec of the behavior you are searching for (the 'tests pinning this behavior' section can only show tests the scope let in)"},
                "compact": {"type": "boolean", "default": False,
                            "description": "default false (code bodies included). Set true for "
                                           "signatures only — drop the code bodies, keep the "
                                           "ranked spans (ids/files/lines/scores)."},
                "docs": {"type": "boolean", "default": False,
                         "description": "default false = search the CODE. true = search the "
                                        "indexed DOCS (markdown) instead. It is one or the "
                                        "other, never a blend: a large README otherwise "
                                        "outranks the implementation it describes."},
                "rerank": {"type": "boolean", "default": True,
                           "description": "default true: a cheap LLM pass drops vocabulary-only "
                                          "matches (tests/evals/tangential files) and reorders "
                                          "(~1-2s). Fails open to the deterministic list. "
                                          "false = pure deterministic retrieval (~200ms)."},
            },
            "required": ["repo_path", "task"],
        },
    },
    {
        "name": "megabrain_grep",
        "description": (
            "Literal search that understands what it found — use it INSTEAD OF "
            "plain grep when you know the exact identifier/string. Every match "
            "is resolved against the index and grouped by ROLE: DEFINES (the "
            "symbol's definition site), READS (real code using it — ranked by "
            "graph centrality, each with the '← reached from' files whose "
            "import/call edges land on it, i.e. the dependents grep cannot "
            "see), CONFIG/DATA, TESTS, DOCS. One call answers 'where is this "
            "defined, who reads it, who depends on the reader' — the three "
            "greps you were about to run, already joined. A caller that is "
            "ABSENT from a read site's reached-from list is a finding, not a "
            "gap (that missing edge has been the bug). Literal by default; "
            "regex=true for patterns. Zero LLM, no vectors, ~50ms."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "path to the indexed repo root (a sub-path also works — the root is auto-detected from .megabrain)"},
                "pattern": {"type": "string", "description": "the exact string to find (literal by default; set regex=true for a regex)"},
                "regex": {"type": "boolean", "default": False,
                          "description": "treat pattern as a regex (default: literal substring)"},
                "ignore_case": {"type": "boolean", "default": False},
                "scope_path": {"type": "string",
                               "description": "optional repo-relative folder to scope matches to files under it"},
            },
            "required": ["repo_path", "pattern"],
        },
    },
    {
        "name": "megabrain_graph",
        "description": (
            "The indexed repo as a NAVIGABLE KNOWLEDGE GRAPH — no LLM in the "
            "structure (AST import/call edges + embedding-similarity edges; the "
            "only LLM touch is cached community labels). mode='map' (default): "
            "labeled communities, god nodes (core abstractions by degree) and "
            "surprising connections (similar code with no structural link) — the "
            "repo overview to start any unfamiliar codebase with. mode='node': "
            "one file resolved from a path OR a concept (embedding lookup) — its "
            "community, structural in/out edges, semantically-close files, "
            "symbols, and its REAL chunks spliced verbatim. mode='path': BFS "
            "route between two concepts showing what carries each hop."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "path to the indexed repo root (a sub-path also works — the root is auto-detected from .megabrain)"},
                "mode": {"type": "string", "enum": ["map", "node", "path"],
                         "default": "map",
                         "description": "map = communities overview · node = one file/concept in depth · path = route between two concepts"},
                "node": {"type": "string",
                         "description": "mode=node: file path or natural-language concept (resolved by embedding)"},
                "source": {"type": "string", "description": "mode=path: start file/concept"},
                "target": {"type": "string", "description": "mode=path: end file/concept"},
                "scope_path": {"type": "string",
                               "description": "optional repo-relative folder to scope the graph to files under it"},
            },
            "required": ["repo_path"],
        },
    },
    {
        "name": "megabrain_index",
        "description": (
            "Index or incrementally update a repo before querying a NEW one (fast: "
            "only changed files are re-embedded; ask/search auto-refresh a stale "
            "index). With list=true (or no repo_path) it instead returns EVERY repo "
            "indexed on this machine — the global registry — so you can discover "
            "what is already searchable without guessing paths."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string",
                              "description": "path to the repo root; omit together with list=true"},
                "list": {"type": "boolean",
                         "description": "true = return the registry of every indexed repo on this machine (no indexing)"},
            },
        },
    },
    {
        "name": "megabrain_forge",
        "description": (
            "Make megabrain index file types it currently can't (COVERAGE forge). "
            "Detects the repo's uncovered text extensions (e.g. .toml, .yaml, .astro, "
            ".proto), then for each one an LLM writes a chunking strategy from real "
            "sample files, only accepted after chunking EVERY matching file with a "
            "clean exact-line partition (repair loop on failure — nothing unvetted "
            "installs). The vetted strategy lands in .megabrain/strategies/, trusted, "
            "and loads on every future index. Use when queries miss content because "
            "its file type isn't indexed. list_only=true = free census; dry_run=true = "
            "inspect the generated code without installing. specialize=true returns "
            "the census of poorly-chunked ALREADY-covered files (NOTE: the LLM path "
            "for specialization was removed — it lost to a deterministic recipe; "
            "hand-write a strategy into .megabrain/strategies/ and gate it with the "
            "Python API megabrain.forge.specialize.gate_strategy, which installs it "
            "only on a measured retrieval win). ~10-60s per extension when generating."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "path to the repo root"},
                "ext": {"type": "string",
                        "description": "forge one extension only, e.g. '.toml'; omit to forge every detected candidate"},
                "list_only": {"type": "boolean",
                              "description": "just return the census (no LLM call)"},
                "dry_run": {"type": "boolean",
                            "description": "generate + validate but do not install or reindex; the report includes the generated code"},
                "specialize": {"type": "boolean",
                               "description": "return the census of poorly-chunked COVERED files (no LLM; strategies are hand-written + gated via gate_strategy)"},
            },
            "required": ["repo_path"],
        },
    },
    {
        "name": "megabrain_flows",
        "description": (
            "Manage the self-caching workflow retrieval for a repo (ON by default). "
            "Every megabrain_ask caches its cross-file walkthrough and the next "
            "related question retrieves the whole workflow at once — a near-exact "
            "repeat serves the cached answer with NO LLM (~0 ms), guarded by a "
            "per-file sha recheck so it can never describe changed code. No extra "
            "call needed: it rides megabrain_ask/megabrain_search. Actions: 'list' "
            "shows what's cached (id · question · cited files · when · stale) — the "
            "repo's accumulated knowledge, so you can see what a teammate or an "
            "earlier session already asked instead of re-asking it; 'get' returns ONE "
            "cached walkthrough in full by id (prose + the real code spliced at cache "
            "time) — free, no LLM, no retrieval; 'delete' drops one by id; 'warm' "
            "discovers the repo's main workflows and pre-caches them with N research "
            "asks; 'refresh' re-asks stale flows against the current code (UPDATE, "
            "not just expire); 'disable' opts the repo out / 'enable' opts back in "
            "(MEGABRAIN_FLOW_CACHE=0 kills it globally). "
            "Use 'warm' once on a repo an agent team will work in, so its workflows "
            "are searchable from the first question."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "path to the repo root"},
                "action": {"type": "string",
                           "enum": ["list", "get", "delete", "warm", "refresh", "enable", "disable"],
                           "description": "default 'list'"},
                "id": {"type": "integer",
                       "description": "for action='get'/'delete': the flow id from action='list'"},
                "n": {"type": "integer",
                      "description": "for action='warm': how many top workflows to discover + cache (default 6)"},
            },
            "required": ["repo_path"],
        },
    },
]


def _scope(args: dict) -> tuple[Path, str | None]:
    """Resolve repo_path (+ optional scope_path) to (repo_root, path_filter) for
    PATH-SCOPE (thin wrapper over app.resolve_scope, kept as the documented MCP
    entry the tests import). repo_path may itself be a sub-path inside an indexed
    repo; an explicit `scope_path`/`subpath` arg is appended to it."""
    from .. import app
    return app.resolve_scope(args["repo_path"],
                             args.get("scope_path") or args.get("subpath"))


def call_tool(name: str, args: dict) -> str:
    from .. import app
    if name in ("megabrain_search", "megabrain_query"):
        # megabrain_query = deprecated 0.9 alias (dispatch only — not in TOOLS,
        # so it costs no agent context; registered clients keep working).
        # ALWAYS the pruned, flat signal list. The file-grouped bundle rendered
        # RELATED as a code-less map, which is a dead end over MCP (there is no
        # get/chunks tool to expand it) — and pruning keeps every bundle file
        # anyway, each with its best chunk.
        #
        # What pruning DOES cost is chunk-level completeness: a CORE file's
        # other chunks are dropped by the keep-ratio cut. That is a deliberate
        # trade — context is the agent's scarce resource — and it is only safe
        # because the agent has Read for the full file, which is why the tool
        # description SAYS so. Claiming "nothing is lost" (it used to) talks an
        # agent out of the one fallback that makes the trade work.
        from ..retrieval.render import render_pruned
        root, pf = _scope(args)
        with_text = not bool(args.get("compact"))
        res = app.prune(root, args["task"], path_filter=pf, with_text=with_text,
                        llm_rerank=bool(args.get("rerank", True)),
                        docs=bool(args.get("docs")))
        return render_pruned(res, with_text=with_text)
    if name == "megabrain_ask":
        from ..ask import render_ask
        root, pf = _scope(args)
        # MCP is request/response — the consuming agent only reads the final
        # text, so the fan-out runs buffered (no streaming) and the trace
        # lands as a one-line footer.
        out = app.ask(root, args["question"], path_filter=pf,
                      docs_only=bool(args.get("docs")),
                      agents=args.get("agents"))
        text = render_ask(out, page=int(args.get("page") or 1))
        if out.get("agents"):
            tr = " · ".join(f'{a["label"]}({len(a["files"])}f)'
                            for a in out["agents"])
            text += f"\n\n— multi-agent: {tr}"
        return text
    if name == "megabrain_grep":
        from ..retrieval.grepx import render_grep
        root, pf = _scope(args)
        res = app.grep(root, args["pattern"], regex=bool(args.get("regex")),
                       ignore_case=bool(args.get("ignore_case")),
                       path_filter=pf)
        return render_grep(res)
    if name == "megabrain_graph":
        from ..graph import render_graph
        root, pf = _scope(args)
        res = app.graph(root, mode=args.get("mode", "map"),
                        node=args.get("node"), source=args.get("source"),
                        target=args.get("target"), path_filter=pf)
        return render_graph(res)
    if name == "megabrain_index":
        if args.get("list") or not args.get("repo_path"):
            from ..storage.registry import list_repos
            return json.dumps({"repos": list_repos()}, indent=1)
        root = Path(args["repo_path"]).expanduser().resolve()
        return json.dumps(app.index(root))
    if name == "megabrain_forge":
        root = Path(args["repo_path"]).expanduser().resolve()
        if args.get("specialize"):
            # LLM specialization was removed (it lost to a deterministic recipe).
            # Report opportunities; strategies are hand-written + gate_strategy().
            from ..forge.specialize import detect_specialization
            return json.dumps({"opportunities": detect_specialization(root),
                               "note": "LLM specialization removed; write the "
                               "strategy into .megabrain/strategies/ and gate it "
                               "with megabrain.forge.specialize.gate_strategy()"}, indent=1)
        from ..forge import detect, forge, render_report
        if args.get("list_only"):
            return json.dumps(detect(root), indent=1)
        report = forge(root, ext=args.get("ext"),
                       dry_run=bool(args.get("dry_run")), quiet=True)
        text = render_report(report)
        for e in report.get("forged", []):
            if e.get("code"):                # dry-run: show what would install
                text += f"\n\n--- generated {e['ext']} strategy ---\n{e['code']}"
        return text
    if name == "megabrain_flows":
        # cache MECHANICS live in storage.flows; the LLM warm/refresh
        # orchestration lives up in ask.warmup (storage never imports upward).
        # list/get/delete go through app.* — the same use-cases serve-api calls,
        # so the two surfaces can never drift.
        from ..storage.flows import set_enabled
        root = Path(args["repo_path"]).expanduser().resolve()
        action = args.get("action", "list")
        if action == "warm":
            from ..ask.warmup import warm_flows
            return json.dumps(warm_flows(root, limit=int(args.get("n", 6))), indent=1)
        if action == "refresh":
            from ..ask.warmup import refresh_stale
            from ..indexing.indexer import index_repo
            index_repo(root, prune_flows=False)
            return json.dumps(refresh_stale(root), indent=1)
        if action in ("enable", "disable"):
            set_enabled(root, action == "enable")
            return json.dumps({"flow_cache": action == "enable", "repo": root.as_posix()})
        if action in ("get", "delete"):
            fid = args.get("id")
            if not isinstance(fid, int):
                from ..errors import MegabrainError
                raise MegabrainError(f"action='{action}' needs an integer `id` "
                                     "(run action='list' to see the ids)")
            if action == "delete":
                return json.dumps(app.flow_delete(root, fid))
            fl = app.flow_get(root, fid)
            # the stored walkthrough is the payload — render it readable, not
            # JSON-escaped, so the consuming agent reads it like an ask answer
            return (f'# cached flow [{fl["id"]}] — "{fl["question"]}"\n'
                    f'cited: {", ".join(fl["files"])}\n\n{fl["text"]}')
        return json.dumps(app.flows_list(root), indent=1)
    from ..errors import UnknownTool
    raise UnknownTool(f"unknown tool {name}")


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
                      "serverInfo": {"name": "megabrain", "version": __version__},
                      "instructions": INSTRUCTIONS}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = msg.get("params", {})
            try:
                text = call_tool(p.get("name", ""), p.get("arguments", {}))
                result = {"content": [{"type": "text", "text": text}]}
            except MegabrainError as e:  # typed -> stable machine code in the text
                result = {"content": [{"type": "text",
                                       "text": f"error ({e.code}): {e}"}],
                          "isError": True}
            except Exception as e:       # noqa: BLE001 — local stdio, msg is useful
                result = {"content": [{"type": "text", "text": f"error: {e}"}],
                          "isError": True}
        elif mid is None:
            continue  # notification
        else:
            result = {}
        if mid is not None:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
