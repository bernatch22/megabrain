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

The implement/fix loop, ONE discovery call: megabrain_search returns the task's whole surface WITH bodies — the render IS your read; NEVER re-fetch a span it showed. megabrain_read only for spans rendered as POINTERS (omitted/set-aside/docs/tests), one batch (auto-splits). Then pinning tests FIRST -> ONE megabrain_replace built from the rendered code -> gates. No host Read/Edit, no grep.

Which tool:
- megabrain_map — FIRST call for any task: files ranked, spans, symbol outline, edges both ways, def sites, pinning tests. No bodies, judge-ranked.
- megabrain_read — batch fetch: ALL read targets in ONE call (path, path#symbol, path:start-end). Verbatim, true line numbers.
- megabrain_replace — batch exact-string edits in ONE call, transactional: validates every op first, any failure writes NOTHING. Existing files only (Write for new).
- megabrain_grep — exact identifier/string: matches grouped into DEFINES / READS (ranked by dependents, with who-reaches-it edges) / CONFIG / TESTS / DOCS. Zero LLM, ~50ms.
- megabrain_search — the task's whole surface WITH code bodies (`N→` gutter): ranked chunks + set-aside sites, doc sections, changelog, pinning tests. Replace directly from it; read only its pointers.
- megabrain_ask — the flow narrated across subsystems, code spliced in (broad questions fan out into sub-agents). Spliced CODE is verbatim; the PROSE is narration — verify its claims against that code.
- megabrain_graph — communities, core abstractions, how two areas connect.
- megabrain_index — register/refresh a repo (auto-refreshes when stale).
- megabrain_flows — cached ask walkthroughs.
- megabrain_forge — add a chunker for an uncovered file type.

TRUST the CODE, verify the PROSE: never grep or re-read what a render already showed. ONE scoped call, then work from it.

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
            "ONE call that returns a task's WHOLE surface, code included: "
            "relevance-ranked chunks WITH bodies (true line numbers, the `N→` "
            "gutter), plus the set-aside sites a change must touch "
            "(constructor/declaration, serialization), the doc sections that "
            "describe the mechanism, the changelog, and the tests pinning the "
            "behavior. EVERY related file appears with its best span. The "
            "render IS your read: build megabrain_replace find/replace "
            "strings straight from it and NEVER re-fetch a span it already "
            "showed. megabrain_read is only for what rendered as a POINTER "
            "(omitted body, set-aside, doc section) — batch those in ONE "
            "call. Do not search again for facets of the same task. "
            "One boundary to know: retrieval "
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
                "bodies": {"type": "boolean", "default": True,
                           "description": "default true: code bodies render inline — the "
                                          "result is your read. false = spans-only surface "
                                          "card (file:start-end + symbols), for when you only "
                                          "want the map of the mechanism."},
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
                "expand": {"type": "boolean", "default": True,
                           "description": "default true: one cheap LLM call names the "
                                          "mechanism identifiers your query lacks and a second "
                                          "deterministic pass widens the pool with them before "
                                          "the judge — kills the follow-up search/grep round "
                                          "(fails open)."},
                "model": {"type": "string",
                          "description": "optional model pin for the judge/expander calls."},
            },
            "required": ["repo_path", "task"],
        },
    },
    {
        "name": "megabrain_map",
        "description": (
            "THE first call for any implement/fix task: a task-level structure "
            "card with NO code bodies — the token-optimal workflow is map -> "
            "ONE batched message of Reads (every target at once, each once) -> Edit. Give it the task in natural "
            "language (or an identifier); it returns the relevant files ranked, "
            "each with match-span pointers (exact L ranges for a surgical "
            "Read), the AST-level symbol outline (signatures + line ranges), "
            "the import/call edges BOTH ways (who reaches this file, what it "
            "reaches), exact identifiers from your query resolved to their "
            "definition sites, and the tests pinning the behavior. A cheap "
            "LLM judge reorders the near-tied head so mechanism outranks "
            "files that merely FORMAT the symptom (fails open to the "
            "deterministic order). Grep-priced output (~50 lines), never a "
            "body. Bodies from megabrain_search are only worth it for files "
            "you will NOT open — the host requires Read before Edit, so a "
            "body of an edit target gets paid twice."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "path to the indexed repo root (a sub-path also works — the root is auto-detected from .megabrain)"},
                "query": {"type": "string", "description": "the task in natural language, or an exact identifier"},
                "scope_path": {"type": "string",
                               "description": "optional repo-relative folder to scope the map to files under it"},
                "rerank": {"type": "boolean", "default": True,
                           "description": "default true: a cheap LLM judge reorders the "
                                          "near-tied head — mechanism over symptom-formatting "
                                          "(~1-2s, fails open). false = pure deterministic "
                                          "(~300ms)."},
                "expand": {"type": "boolean", "default": True,
                           "description": "default true: one cheap LLM call names the "
                                          "mechanism identifiers your query lacks and a second "
                                          "deterministic pass widens the pool with them before "
                                          "judging — the LLM names search terms, never spans "
                                          "(fails open)."},
                "model": {"type": "string",
                          "description": "optional model pin for the judge/expander calls "
                                         "(default: the measured fast-lane rerank model)."},
            },
            "required": ["repo_path", "query"],
        },
    },
    {
        "name": "megabrain_read",
        "description": (
            "Batch read: EVERY read target of the whole task in ONE call, "
            "verbatim from disk with true line numbers. Three spec forms per "
            "target: 'path' (whole file, capped — big files must narrow), "
            "'path#symbol' or 'path#sym1,sym2' (the symbol's exact line range, "
            "resolved from the index), 'path:120-180' (explicit line range). "
            "Use the map's spans/symbols as targets instead of whole files. "
            "This replaces one-file-per-turn host Reads: list edit targets, "
            "the tests you will touch and docs/changelog in a single call, "
            "then edit with megabrain_replace. Oversized batches auto-split: "
            "what fits renders now, the tail comes back as a ready re-call "
            "list under 'NOT RENDERED' — issue that one follow-up call."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "path to the indexed repo root"},
                "targets": {"type": "array", "items": {"type": "string"},
                            "description": "specs: 'path', 'path#symbol[,symbol2]', 'path:start-end'"},
            },
            "required": ["repo_path", "targets"],
        },
    },
    {
        "name": "megabrain_replace",
        "description": (
            "Batch exact-string edits in ONE transactional call. Each "
            "operation is {file, find, replace, count?}: `find` must occur "
            "exactly `count` times (default 1) in the current text — add "
            "surrounding lines to make it unique. ALL operations are "
            "validated in memory first; if ANY fails, NOTHING is written and "
            "the report says which op failed and why (with the nearest line "
            "when the text was not found). Edits existing files only — "
            "create new files with the host Write tool. After a successful "
            "replace, run the repo's gates."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "path to the indexed repo root"},
                "operations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "file": {"type": "string", "description": "repo-relative file"},
                            "find": {"type": "string", "description": "exact text to replace (must occur exactly `count` times)"},
                            "replace": {"type": "string", "description": "replacement text"},
                            "count": {"type": "integer", "default": 1,
                                      "description": "exact number of occurrences expected (default 1)"},
                        },
                        "required": ["file", "find", "replace"],
                    },
                    "description": "edits applied in order; same-file ops see the previous op's result",
                },
            },
            "required": ["repo_path", "operations"],
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


# Chunk bodies rendered per repo in this server's lifetime, TTL'd. Parallel
# searches over facets of ONE mechanism overlap by design (click#3652 field
# run: the same get_help_record chunk rendered three times in one message) —
# a body the agent already has renders as a pointer, never twice. The TTL
# keeps a later, unrelated task from inheriting stale dedup.
_SEEN_TTL = 600
_seen: dict[str, tuple[float, set]] = {}


def _seen_chunks(root: Path) -> set:
    import time as _t
    key = str(root)
    ts, ids = _seen.get(key, (0.0, set()))
    if _t.time() - ts > _SEEN_TTL:
        ids = set()
    _seen[key] = (_t.time(), ids)
    return ids


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
        #
        # BODIES BY DEFAULT — the render IS the agent's read. The click
        # aliases duels measured both failure modes: a workflow that forces a
        # read AFTER a bodies search pays every span twice (the arena prompt
        # mandated it, ~2x the plain arm's tokens), and a card-only search
        # forces a read round-trip that a complete render makes unnecessary.
        # The winning flow is ONE search whose bodies are complete enough to
        # replace FROM directly; megabrain_read exists for the spans the
        # render left as pointers (omitted/set-aside/docs), one batch, only
        # when needed. `bodies: false` opts into the spans-only card.
        # prune always loads text (judge/expander evidence — same stance as
        # the map); `bodies` only controls the RENDER.
        from ..retrieval.render import render_pruned
        root, pf = _scope(args)
        bodies = (args.get("bodies", True) is not False
                  and not bool(args.get("compact")))
        res = app.prune(root, args["task"], path_filter=pf, with_text=True,
                        llm_rerank=bool(args.get("rerank", True)),
                        expand=bool(args.get("expand", True)),
                        model=args.get("model"),
                        docs=bool(args.get("docs")),
                        with_docs=not bool(args.get("docs")))
        return render_pruned(res, with_text=bodies,
                             seen_ids=_seen_chunks(root))
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
    if name == "megabrain_map":
        from ..retrieval.mapcard import map_repo, render_map
        root, pf = _scope(args)
        return render_map(map_repo(root, args["query"], path_filter=pf,
                                   rerank=bool(args.get("rerank", True)),
                                   expand=bool(args.get("expand", True)),
                                   model=args.get("model")))
    if name == "megabrain_read":
        from ..retrieval.readx import read_specs, render_read
        root, _ = _scope(args)
        return render_read(read_specs(root, list(args["targets"])))
    if name == "megabrain_replace":
        from ..retrieval.replacex import apply_ops, render_replace
        root, _ = _scope(args)
        # `operations` is canonical; tolerate `edits`/`ops` and a JSON string
        ops = args.get("operations") or args.get("edits") or args.get("ops")
        if isinstance(ops, str):
            import json as _json
            ops = _json.loads(ops)
        return render_replace(apply_ops(root, list(ops or [])))
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
