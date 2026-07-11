# Changelog

## 0.7.1 — 2026-07-11

- **Fix: CommonJS/prototype methods (`obj.prop = function(){}`) were invisible
  to the JS chunker.** The TS/JS spec captured only `function_declaration` /
  `method_definition` / `lexical_declaration`, so express's entire router API —
  `proto.use`, `proto.handle`, `Route.prototype.dispatch`, … — produced NO
  symbols: unlabelable in `ask` (citations fell back to listing the file's
  `require()` consts, e.g. "appendMethods, getPathname, gettype"), and absent
  from the file skeleton used in scoring. New `assign_defs` spec flag (on for
  TS/JS) captures `member = function/arrow` assignments as method symbols named
  by their full LHS (`Route.prototype.dispatch`). Verified on express: the
  `next()` walkthrough now labels every citation correctly
  (`proto.handle`, `Layer.prototype.handle_request`) — line partition
  unchanged, all chunker tests green.
- **Fix: `ask` sub-range citations landed a few lines off, cutting functions
  mid-body.** The prompt showed each chunk's text RAW with only a header line
  range, so the model had to count lines itself to cite `[[k:lo-hi]]` — cites
  started on a neighbor's trailing lines and stopped mid-method. Chunk text in
  the prompt is now prefixed with absolute file line numbers (`1234| code`,
  prompt-only — splicing still uses the clean text from disk) and the rules
  require reading lo/hi off those prefixes and citing complete units (signature
  → closing line). Verified on sinatra's routing walkthrough: 8/8 citations now
  open at `def` and close at its `end` (before: mid-method cuts and orphan
  tails).
- **Fix: Ruby `class << self` regions chunked blind.** `singleton_class` was
  missing from `RUBY_SPEC` (not a container, not a def type), so the whole
  region — sinatra's entire `get/post/route/compile!` DSL — became anonymous
  size-packed `block` chunks with NO symbols: unnamed in rankings, unlabelable
  and unsnappable in `ask` citations. Now a named container (`self`, via the
  node's `value` field): methods inside become real symbols
  (`Sinatra.Base.self.get`), merged chunks carry names, and citation
  snap-to-symbol works there.

- **Fix: the test-file down-weight missed `test/` (singular) and `spec/`
  directories.** The detector checked only the SECOND path component for the
  substring "test" plus `tests/` (plural), so repos laid out as `test/…`
  (express, ky) or `spec/…` (Ruby) never received `TEST_PENALTY` — test files
  outranked the core they exercise ("how are retries and timeouts implemented?"
  on ky returned `test/retry.ts` above `source/core/Ky.ts`). New `_is_test_path`:
  any path segment named `test/tests/spec/specs/__tests__/testing`
  (segment-exact, never substring) or a token-ish `test`/`spec` in the filename
  (`foo_test.go`, `test_foo.py`, `foo.spec.ts` — but not `inspect.py`).
  Golden set unchanged: R@1 0.864, bundle_full 0.955.

## 0.7.0 — 2026-07-11

- **Serve-from-cache: a repeated `ask` costs $0 and ~0 ms.** Flows now store the
  RENDERED answer (prose + real code spliced from disk) and TWO vectors —
  question+prose (the attach lane) and question-only (the serve lane, so prose
  length can't dilute an identical question). When a question near-exactly
  matches a cached flow (qscore ≥ 0.88) AND every cited file is still
  byte-identical (sha recheck at serve time — stale code is never served), the
  cached answer returns verbatim with no LLM call: measured 6.9 s → **0.02 s**
  (345×) on a repeat ask; a re-worded near-exact variant also serves. Wired in
  `ask()` (MCP/library) and `stream_ask` (CLI); `render_ask` shows "⚡ served
  from flow cache". Dedup now keys on the question lane (two narrations of one
  question replace, not accumulate). Paraphrases in the 0.62–0.88 band attach
  as context and narrate fresh, as before.
- **Default ask model → `google/gemini-3-flash-preview`** — measured ~2× faster
  than qwen3-coder on a real walkthrough (~6-7 s vs ~14 s) at comparable
  quality ($0.50/$3.00 vs $0.22/$1.80 per M). `MEGABRAIN_ASK_MODEL=qwen/
  qwen3-coder` for the cheapest/broadest-citation option; Claude provider
  default unchanged (haiku).
- **docs/GUIDE**: query-vs-ask decision table (aimed at LLM agents calling the
  MCP), the three flow-cache tiers with the measured numbers, updated model
  table.

- **Flow cache — self-caching workflow retrieval** (`megabrain/flows.py`).
  **Opt-in, OFF by default** — a mode a dev turns on per repo
  (`megabrain flows --enable`, implied by `--warm-flows`; env
  `MEGABRAIN_FLOW_CACHE` forces on/off globally). When off, `query`/`ask`
  behave exactly as before at zero cost (load_state skips flows entirely).
  When on: every successful `ask` synthesizes a cross-file walkthrough (a workflow:
  "VAD detects speech → TurnController.on_vad_start → cancel TTS") that the
  engine used to throw away. Now it is cached in the index (`flows` table:
  question + prose + {cited file: sha} + embedding) and the NEXT related
  question retrieves the whole flow at once — validated: a barge-in flow
  cached from one question was retrieved by a fully re-worded paraphrase.
  Design keeps every hard rule intact: the LLM and the one embed call happen
  at ASK time (write path); the read path is pure cosine against the flow
  matrix, reusing the query vector already computed (no second embed, no LLM).
  Flows ATTACH to the bundle (a "KNOWN FLOW" section + non-citable context for
  the narrator) and never rank or displace files — their source files append
  to RELATED only when missing, pure additions, so bundle_full can only rise.
  Invalidation: index_repo prunes any flow whose cited files changed sha, so a
  stale walkthrough cannot outlive the code it describes (and `ask` splices
  real code from disk regardless — a stale flow can mis-prioritize, never
  fabricate). Near-duplicate flows replace instead of piling up. **Warmup**
  (opt-in): `megabrain index --warm-flows N` / `flows --warm N` — right after
  the first index, an index-time LLM planner reads the graph's hub files and
  writes N research questions covering the system's main workflows, then runs
  one `ask` each, so the cache starts full instead of building up lazily. CLI:
  `megabrain flows <repo> [--enable|--disable|--warm N|--clear]`; kill switch
  `MEGABRAIN_FLOW_CACHE=0`. **Refresh, not just expire** — `megabrain flows
  --refresh` re-asks each stale flow's ORIGINAL question against the current
  code and regenerates the walkthrough (opt-in: one `ask` per changed flow),
  so the cache stays *current* rather than only *not-wrong*; `index_repo`
  gained `prune_flows=False` so refresh can reindex-then-regenerate without the
  default prune dropping the flows first. Related literature: Knowledge
  Compression via Question Generation (arxiv 2506.13778) — indexing synthesized
  knowledge lifts multi-hop retrieval.
- **New: [docs/GUIDE.md](docs/GUIDE.md)** — a step-by-step usage guide
  (providers with options, indexing, the 2000-vs-4000 budget choice, how the
  engine measures a strategy, the flow cache).

- **Removed LLM-generated specialization strategies.** Across four repos
  (sinatra, requests, sdk-server, the engine itself) an LLM asked to write a
  specialization chunker consistently LOST — to a five-line deterministic
  recipe (`lit_baseline`: the AST chunker re-budgeted to 2000) and to the plain
  4000 default. `forge_specialize` no longer calls a model; it is now a
  measurement toolkit for HAND-WRITTEN strategies: `detect_specialization`
  (where the built-in chunks poorly), `lit_baseline` (the reference to beat),
  and `gate_strategy(root, source, ext)` — measure a hand-written chunker with
  `forge_eval.ab_gate` and install it trust-gated only if it wins. CLI
  `forge --specialize` now only lists opportunities; the MCP `specialize` mode
  returns opportunities + a note. (Coverage `forge` for UNCOVERED extensions is
  unchanged.)
- **Documented the sacred-bar finding.** On the sdk-server golden set (the one
  corpus with human-verified queries), no chunk budget beats 4000: R@1 4000=0.86,
  2000=0.82, surgical blob-splitting=0.77. Tighter chunks improve span-IoU
  (navigation — less to read) but LOWER retrieval ranking, because the 4000 merge
  concentrates a file's evidence and that is what wins R@1. `DEFAULT_BUDGET`
  stays 4000; specialization is an honest win only for its navigation objective.

- **`forge --specialize` — chunkers tuned to a repo's own conventions**
  (`megabrain/forge_specialize.py` + `megabrain/forge_eval.py`; CLI
  `megabrain forge --specialize [--list|--dry-run|--ext .x]`, MCP
  `megabrain_forge` `specialize` param). Coverage forge teaches the engine file
  types it can't read; specialization re-chunks types it ALREADY reads where
  the generic chunker fits poorly — a module that is one giant lookup table
  becomes a blob, so a query about one entry retrieves the whole file. The
  detector diagnoses three shapes (dominant dict/list table, blob, line-window
  fallback); parallel LLMs write **shape-routers** (split the diagnosed shape
  into tight named chunks, delegate every normal file to the built-in
  byte-identically via the new `builtin_strategy_for`). Because a
  partition-valid chunker can still be *worse* than the built-in, installs are
  gated by a **measured retrieval A/B** (`forge_eval.ab_gate`): neutral probe
  spans derived from the file's own structure (no labels, no LLM), both
  variants indexed for real, and **rank-aware span-IoU** — the file's
  top-ranked chunk vs the true span, what retrieval actually surfaces — plus
  global hit@k scored on every file the candidate changes. Win requires the
  pooled IoU lift ≥ 0.01 AND hit@1 held AND no per-file regression AND no
  micro-chunking (median chunk ≥ 100 nws, rejected before any indexing); a
  losing candidate gets one regeneration seeded with the measured result. The
  strict gate earned its clauses in the wild: a candidate that scored a fake
  "0.55 IoU win" via median 1-line chunks measures Δ-0.001 with hit@1
  regressing under it, and is rejected. Wins that survive: psf/requests
  `status_codes.py` IoU 0.010 → 0.076 / hit@1 0.23 → 0.47 (2×); sinatra `.rb`
  IoU 0.037 → 0.115 with zero per-file regressions — all other files
  byte-identical in both.

## 0.6.0 — 2026-07-11

- **`forge` — megabrain writes its own chunkers** (`megabrain/forge.py`). CLI
  `megabrain forge [--list|--dry-run|--ext .x]`, MCP `megabrain_forge`. Detects
  the repo's uncovered text extensions (deterministic census), LLM-generates a
  `ChunkStrategy` per type from the contract source + real samples (the `ask`
  provider stack; `MEGABRAIN_FORGE_MODEL` to pin), and installs it only after it
  chunks EVERY matching file with a clean `validate_partition` (repair loop ≤3
  attempts — unvetted code can never install). Verified on pallets/click: `.toml`
  + `.yaml` forged first-attempt in ~28 s; "which workflow runs the tests" went
  from a full miss to `.github/workflows/tests.yaml` #1.
- **Repo-local strategies, trust-gated** (`indexing/strategies.py`). Vetted
  modules in `<repo>/.megabrain/strategies/*.py` load automatically on every
  `index_repo` — including the 60 s auto-refresh, which previously pruned
  custom-extension files as orphans. Loading only happens when the module's
  sha256 matches `~/.megabrain/trust.json` (user-level — a cloned repo cannot
  self-approve); `megabrain trust <repo>` approves hand-written modules, and any
  edit un-trusts the file until re-approved.

## 0.5.0 — 2026-07-06

- **`ask v2` — adaptive multi-agent synthesis** (`megabrain/ask_agents.py`).
  When a question is broad and single-shot retrieval isn't confident, `ask`
  fans out: a no-LLM classifier reads the bundle shape, a planner splits it
  into ≤4 scoped slices, parallel sub-agents (each with the repo map + no-LLM
  retrieval tools `search_more`/`get_file`/`get_symbol`) explain their slice,
  and a parent synthesizes with the same global `[[k]]` citation-splice — code
  stays verbatim. Every stage fails open to single-agent `ask`. Surfaces: CLI
  `ask --agents/--no-agents` (default AUTO), MCP `agents` param, serve-api
  `POST /ask/stream` (SSE live view). Scoped questions never pay for it, and no
  LLM ever enters the retrieval path (rule 1 holds). Gates green: full suite +
  golden (bundle_full 1.00, R@1 0.86) + multi + scale.
- Provider tool-calling: `stream_chat(with_tools=True)` parses OpenAI
  `tool_calls`; the Claude path registers the retrieval tools as an in-process
  SDK MCP server.

## 0.4.1 — 2026-07-06

- **Internal package reorg** — the tree now mirrors the pipeline: `chunkers/` ·
  `indexing/` (indexer, strategies, graph) · `retrieval/` (query, issue, bm25,
  rerank) · `providers/` (chat routing, claude, embeddings) · `frontends/`
  (cli, mcp, http), with `ask.py`/`store.py` at the root. The **public API is
  unchanged** (`megabrain.{index_repo, search, …}`, `megabrain.ask`), and
  `python3 -m megabrain.mcp_server` keeps working via a launcher shim. Deep
  imports of old module paths (`megabrain.query`, `megabrain.indexer`,
  `megabrain.serve`, `megabrain.chunker*`) moved to their new homes.
- Versioning policy going forward: patch-first, publish only when there's a
  reason (see CONTRIBUTING → Releasing).

## 0.4.0 — 2026-07-06

Open-source readiness release. Retrieval behavior is unchanged where it counts:
all three retrieval gates hold the locked bar (golden R@1 0.86 · bundle_full
1.00 · scale p50 < 20 ms).

### Fixed
- **Windows: indexes were corrupt** — relpaths were stored with `\` while the
  whole engine matches on `/` (DB keys, excludes, path filters, graph edges,
  `chunks`/`get` lookups), so nothing resolved. Relpaths are now POSIX on every
  platform. (Caught by the new Windows CI matrix.)

### Security
- `get_code` now enforces repo-root containment — `../` and absolute paths can
  no longer escape the index root (was reachable via `serve-api GET /get` and
  MCP `megabrain_get`).
- `serve-api` gained optional Bearer auth: `--token` / `MEGABRAIN_API_TOKEN`
  guards every endpoint except `/health`; a warning is printed when binding
  beyond localhost without one.

### Changed
- **`query` renders RELATED as a map by default** (file, best-match span,
  symbols — no chunk code bodies; CLI `--full` / MCP `full: true` restores
  them). Measured on the golden set: RELATED holds 45% of the gold files so it
  can't be dropped, but its code bodies were ~16K of a ~22K-token bundle at
  ~5% verified signal — they flooded agent context windows. The bundle DATA is
  unchanged (`ask`/HTTP consumers keep `best_chunk`), all three retrieval
  gates hold (bundle_full 1.00), and a typical bundle drops ~22K → ~8K tokens.
- **Default index excludes trimmed to universal dirs.** `data`, `logs` and
  maintainer-local names are no longer skipped by default — add them to your
  repo's `.megabrainignore` if you relied on that. New defaults add `.tox`,
  `.mypy_cache`, `.ruff_cache`, `target`, `vendor`, `.nuxt`.
- **Chunkers moved to `megabrain.chunkers`** (`base` / `python` / `treesitter`
  / `php` / `markdown`). The old module paths (`megabrain.chunker`,
  `chunker_ts`, `chunker_php`, `markdown`) remain as deprecation shims for one
  release.
- Rerank v1 removed (unused); `rerank2.haiku_order2` is now
  `rerank.llm_order` (the default model has been qwen3-coder since the
  OpenRouter move).
- `/docsearch` result groups are now per-deployment config
  (`.megabrain/docsearch.json` or `MEGABRAIN_DOCSEARCH_GROUPS`) instead of
  hardcoded section names; unmatched slugs group under "Docs".
- CLI: single-path commands now error on comma multi-path input instead of
  silently dropping everything after the first comma.

### Added
- `ask --with-docs` (MCP `include_docs`, HTTP `include_docs`): explain code
  AND docs together — third mode next to the default (code only) and `--docs`
  (docs only).
- CLI `ask`/`query`/`chunks` now auto-refresh a stale index before answering
  (60 s TTL, incremental, fail-open without a key) — previously only the MCP
  server did, so CLI answers could cite stale code after an edit.
- **Claude chat provider** (extra `megabrain[claude]`): `ask`/`--best` stream
  through the Claude Agent SDK — Claude Code **subscription credits** when the
  CLI is logged in, or `ANTHROPIC_API_KEY` for API billing. Default model
  `haiku` (`MEGABRAIN_ASK_MODEL` accepts any Claude model/alias). The chat
  provider **defaults to auto**: Claude when its SDK is importable, else
  OpenRouter — pin with `MEGABRAIN_CHAT_PROVIDER=claude|openrouter`. Embeddings
  are unaffected and still require OpenRouter or a local embed endpoint.
- **Custom chunking strategies**: `index_repo(root, strategies=[MyStrategy()])`
  plugs any content type in without forking (checked before the built-ins, so
  a custom strategy can also override one). New `ChunkStrategy` protocol;
  `Chunk`/`Symbol`/`FileResult`/`validate_partition` exported at top level.
- `examples/`: programmatic API walkthrough, a complete custom `.sql` chunker
  (offline-runnable), and a terminal chunk-score heatmap.
- Lazy public API: `megabrain.{index_repo, search, render, get_code,
  load_state, search_with_state, Store}` (importing `megabrain` no longer
  pulls numpy/tree_sitter) + `py.typed`.
- Issue grounding beyond Python: JS/TS stack frames (`at fn (src/x.ts:12:5)`)
  pin files/spans; explicit `.ts/.tsx/.js/.jsx/.mjs/.cjs/.rb/.go/.rs/.php`
  paths ground like `.py` paths.
- TS import graph: dynamic `import()`, side-effect imports, and
  `.jsx/.mjs/.cjs`/`index.js` resolution.
- `MEGABRAIN_DEBUG=1` surfaces previously-swallowed provider errors.

### Performance
- `ask` loads retrieval state once per question (was: matrices twice + an
  extra SQLite connection) and accepts a warm `state`.
- BM25 scores via postings (only docs containing each term).
- Issue-mode lanes (BM25 + symbol grounding corpus) cached on `SearchState`
  for warm servers.
- `search_multi` queries repos concurrently; embedding cache writes are
  atomic (safe under concurrency).

## 0.3.2
- Legacy-PHP section chunker (banner sections, HTML islands, QMD cuts) with
  shape-routing: modern PSR/namespaced files keep the generic chunker.
- `chunks` CLI command, `megabrain_chunks` MCP tool, `GET /chunks` endpoint:
  every chunk of one file scored for a query, with selected flags.

## 0.3.1
- PHP `use`-statement import graph (PSR-4-agnostic FQCN index).
- Edge-preservation fix: re-indexing a file no longer destroys incoming edges
  indexed earlier in the same pass; GRAPH_EXTRAS retuned 6 → 7.
- Configurable index excludes: `--exclude` + `.megabrainignore`.

## 0.3.0
- PHP support; PyPI packaging; provider abstraction via OpenRouter
  (`MEGABRAIN_EMBED_MODEL` / `MEGABRAIN_ASK_MODEL` / local OpenAI-compatible
  endpoints).
