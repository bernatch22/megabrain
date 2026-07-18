# Changelog

## 0.13.1 — derived questions that read like questions

Measured against the demo box's real repos, 0.13.0's derived tier produced
garbage on every language WITHOUT a module docstring — the skeleton's first
line is already a declaration there, and it was being used as prose:

    How does const ( work end to end?                       (ky)
    How does var _ context.Context = (*Context)(nil) …?     (gin)
    How does func TestRenderJSON(t *testing.T) …?           (gin)

- **A declaration is no longer mistaken for a docline.** Go/TS/Rust files
  fall straight to naming the file by its dominant definition instead.
- **Symbol kinds cover every language.** `type` (Go structs, TS aliases) and
  `interface` were missing from the nameable set, so those languages fell
  through to their constants.
- **The DOMINANT definition names the file, not the first one.** Files open
  with small private helpers (`logerror`, `dict_to_sequence`) — the widest
  span is what the file is about (`Engine`, `Ky`, `Session`, `Context`).
- **Same-package test and generated files are excluded.** Go/JS keep tests
  beside the code (`x_test.go`, `x.test.ts`) and a directory-only filter
  missed them; `.pb.go`/`_pb2.py` generated code is out too.
- **Labels no longer collide.** Every sinatra file's widest symbol is the
  enclosing `Sinatra` module, so dedupe collapsed the whole repo to ONE
  question; each file now takes the best name no earlier file claimed.
- A docline that is a sentence ("click is a simple Python module inspired
  by…") is cut at the copula, so the label is the subject ("click").

## 0.13.0 — every repo gets starter questions

- **`GET /queries` now answers for EVERY indexed repo**, in three tiers, so
  the studio's Ask tab is never a blank box:
  1. `file` — the repo committed a `.megabrainqueries` (authored intent wins).
  2. `flows` — the questions already in the flow cache. The best fallback by
     far: each one's answer is *cached*, so clicking the chip serves
     instantly with no LLM and no rate-limit cost. The UI labels the row
     "⚡ ALREADY ANSWERED · instant, from cache".
  3. `derived` — deterministic, no-LLM questions over the repo's central
     files (`ask.warmup.derive_questions`).
  The response carries `source` so the UI can say honestly where the chips
  came from. Previously only tier 1 existed: a repo without the file got
  nothing.
- **Central-file ranking is language-agnostic.** It was edge degree alone,
  but the import/call graph only covers py/ts/js/php — measured on the demo
  box, ky has **0** edges, sinatra 1, gin 9, so degree-only ranking
  degenerated to arbitrary order on exactly the repos that need help. Ranking
  is now degree *plus symbol density*, and tests/examples/vendored paths are
  excluded from seeding a question.
- **`flows --warm` reuses `.megabrainqueries` when present** instead of
  paying an LLM planner call to guess. Writing the file once now both
  documents the repo's main workflows AND pre-caches exactly the answers the
  studio offers as chips — so every chip serves instantly afterwards.
- The studio hides "Warm all" on a `--readonly` box: one click would burn N
  LLM asks of the host's budget and the visitor's whole rate-limit window.

## 0.12.1 — studio: Ask opens first · clean repo switches

- **Ask is the first tab and the default view.** It's the star of the studio;
  it opened on Search only for historical reasons.
- **Switching repos no longer leaves a stale query behind.** The results were
  cleared but the input kept the previous repo's question, which read as a
  pending request against the new repo (and made the empty Ask view look like
  a broken Search). The query clears with the rest of the per-repo state.

## 0.12.0 — the studio as a public demo: --readonly + --rate-limit

- **`--readonly`** (studio / serve-api): serve the indexed repos, refuse every
  mutating/config route server-side with a 403 (`/index`, `/index/stream`,
  `/repos/add`, `/scan`, `/fs/pick`, `/providers/select`,
  `/providers/ollama/serve`, `/flows/delete` — one `READONLY_BLOCKED` set next
  to the route table). The SAME UI bundle adapts: it reads the new
  `GET /config` (`{readonly, rate_limit, version}`, auth-exempt like /health)
  and hides Add repo, settings/providers, re-index, flow deletes and cold
  registry rows — no forked demo UI, one source of truth; the lock never
  depends on the UI.
- **`--rate-limit N`**: at most N LLM asks (`/ask` + `/ask/stream`) per hour
  per client IP — 429 with the retry seconds. Retrieval routes stay unlimited
  (local, ~free). `--trust-proxy` takes the client IP from X-Forwarded-For
  (off by default: the header is spoofable).
- **The studio now works behind a path prefix.** api.js resolved every route
  absolutely (`/repos`), so nginx-mounting the studio under
  `/megabrain/demo/` broke every fetch; routes now resolve relative to the
  page's directory. At the root nothing changes.
- Together these replace the hand-rolled demo backend on bernardocastro.dev:
  the public demo becomes `pip install megabrain` + `megabrain studio
  --readonly --rate-limit 30 --trust-proxy` behind nginx.

## 0.11.0 — the cache you can read: a Flows tab, starter queries, flows over MCP

- **Studio Flows tab — the ask cache, listed and viewable.** Every cached ask
  (flow) newest-first: question, cited files, when it was cached, `stale`
  when a source changed; click one for the stored walkthrough exactly as it
  will be served, each cited file openable in the code navigator; delete
  inline. New routes `GET /flows` (list, no text), `GET /flow?id=` (full),
  `POST /flows/delete {id}` — thin adapters over new `app.flows_list/
  flow_get/flow_delete` use-cases. Flow rows now record a `created` timestamp
  (in-place ALTER migration, like qvec; old rows show "—").
- **The cache is visible in Ask.** A verbatim serve shows a
  "⚡ served from flow cache" banner (no LLM · retrieval ms · the original
  cached question) and the synthesis header reads "FROM CACHE" instead of a
  model name. Flows that ATTACH as KNOWN-FLOW context (0.62–0.88 match) show
  as "known flows" chips in the info bar — the `retrieval` stream event now
  carries `flows: [{question, score}]`.
- **`megabrain_flows` gains `get` and `delete` (MCP).** The action enum now
  covers `list` (which finally returns flow **ids**, plus `created` and
  `stale`), `get` (one cached walkthrough in full, by id — free: no LLM, no
  retrieval) and `delete`. All three route through the same `app.*`
  use-cases serve-api calls, replacing the hand-rolled Store query that had
  drifted from it. An agent can now read what a teammate already asked
  instead of paying to re-ask it.
- **Staleness is measured against DISK, not the index.** `stale_flows()`
  compares a flow's cited shas to the *index*, which legitimately lags disk
  by up to the 60 s refresh TTL — so a freshly cached, perfectly serveable
  flow showed up as `stale`. The listing now uses the same disk check the
  serve path makes, extracted as `flows.files_current()` and shared by both
  (it was inline in `serve_verbatim`). `Store.stale_flows()` is unchanged —
  index consistency is the right question for the *pruning* path.
- **`.megabrainqueries` — committable starter queries.** One query per line
  (`#` comments) at the repo root; `GET /queries` serves them and the studio
  renders them as one-click chips under the Ask bar, plus an explicit
  **⚡ Warm all** button that runs each starter once (buffered `POST /ask`)
  and caches it as a flow. The onboarding play: a newcomer opens the repo,
  clicks through the starters, and sees the main workflows — instantly if
  someone already warmed them. This repo ships its own `.megabrainqueries`.

## 0.10.0 — the repo as a graph · flow cache on by default · search rerank

- **Flow cache ON by default** (was opt-in since its introduction). Measured
  on this repo: a repeated ask (even reworded) drops from 27.8 s to **0.19 s
  with zero LLM**, and correctness is guarded per serve by a byte-level
  sha256 recheck of every cited file — editing a cited file makes the next
  ask narrate fresh and re-cache, never serve stale. Meta absent = on, so
  existing indexes flip on without a re-index. Opt out per repo with
  `megabrain flows --disable` (persisted), or globally with
  `MEGABRAIN_FLOW_CACHE=0` (the kill beats a per-repo enable). The two costs
  this trades: `ask` now writes to the repo's `.megabrain/db.sqlite` (one
  embed + one INSERT), and related questions may attach up to 3 flow-source
  files to the bundle (pure additions, never displacing ranked files).
  `--warm-flows` / `flows --warm` stay explicit commands (they cost real LLM
  asks); warming re-enables an opted-out repo.
- **`megabrain_graph` — the repo as a navigable knowledge graph (new MCP tool
  / CLI verb / `GET /graph` / studio tab).** Built from what indexing already
  owns: AST import/call edges (structural lane) + skeleton-embedding cosine
  (semantic lane — similar files with no import between them, honestly
  scored). Deterministic weighted label propagation for communities (numpy
  only, no networkx), god nodes by degree, "surprising connections"
  (cosine ≥0.85 + no structural edge + different communities), BFS paths
  with the carrying edge kind per hop, and endpoints resolved by EMBEDDING —
  `megabrain graph . --node "the scoring pipeline"` lands on `scoring.py`.
  The one LLM touch is community labeling: a single buffered call, cached in
  the store's meta under a graph fingerprint, fail-open to "Community N".
  Node views splice the store's REAL chunks (new `Store.all_edges()` /
  `Store.file_chunks()`). Measured: this repo 122 files/324 links in 8ms;
  graphify's own 630-file repo in 37ms. Where graphify needs LLM sub-agents
  to extract relationships, megabrain gets the graph for free at index time.
- **`megabrain_query` → `megabrain_search`** (breaking, with a net): the tool
  IS a search and the engine already speaks that vocabulary
  (`search_with_state`/`prune_search`); "query" read as SQL. TOOLS lists only
  `megabrain_search`; `call_tool` still accepts `megabrain_query`, so
  registered 0.9 clients keep working at zero agent-context cost. CLI:
  `megabrain search` is the primary verb, `query` a hidden alias.
- **LLM rerank on search (`llm_prune`)** — the deterministic prune is
  recall-safe by design, so files that merely share vocabulary with the query
  (tests, evals, A/B gates) survive as signal. A cheap buffered LLM call now
  sees a compact candidate listing (ids + spans + hints, never bodies) and
  returns the relevant ids ordered; the engine keeps its own verbatim chunks
  (the model selects, never writes code) and ANY failure returns the
  deterministic result untouched. Defaults: MCP `rerank: true`, CLI
  `search --rerank` opt-in (keeps the 2ms path), `GET /prune?rerank=1`.
  Model: `MEGABRAIN_RERANK_MODEL` (else the ask default). Measured on the
  motivating query ("how does retrieval scoring work"): 21 signal chunks → 6,
  the three scoring.py lanes ranked 1-2-3, every eval/test tangential dropped.
- **Global repo registry** — every `index_repo` now registers its repo in
  `~/.megabrain/registry.json` (override `MEGABRAIN_REGISTRY`; atomic writes;
  fail-open; self-healing — entries whose index vanished are dropped on
  read). Surfaces: `megabrain repos` (CLI), `megabrain_index list=true`
  (MCP), and `GET /repos` merges the server's warm sessions
  (`loaded: true`) with registry-only repos (`loaded: false`) so the studio
  rail shows EVERY indexed repo on the machine — clicking a cold one loads it.
- **Studio: Graph tab** — a force-directed canvas (vanilla, no libs) over
  `/graph`: nodes colored by community with labels at centroids, god nodes
  haloed, solid structural vs dashed semantic edges, drag/zoom/pan, click a
  node for its neighbors + symbols + real chunks, and `A -> B` in the query
  bar traces a highlighted path. Physics stays cheap via per-community
  repulsion (no global O(n²)). Prune view gains the LLM-rerank toggle.
- **Studio: the add-repo file tree, rebuilt.** The old tree re-rendered the
  whole overlay on every toggle, so each click threw the scroll position back
  to the top, and re-including one file under an excluded folder was flatly
  refused ("re-include the parent folder first"). Now: a targeted repaint
  keeps scroll and input focus; whole rows are clickable (folders expand,
  files toggle, VS-Code style); re-including a child performs a **rule split**
  — the excluded ancestor is replaced by exclusions of its siblings, so the
  selection stays expressible as plain `.megabrainignore` lines (which have no
  `!` negation). Adds tri-state folder checkboxes with real `included/total`
  counts, a filter box with match highlighting and auto-expansion of hits,
  full keyboard navigation (↑↓ move, →← expand/collapse, space toggles),
  All/None/Expand/Collapse actions, indent guides, and a footer stating how
  many ignore rules the choice will write. Light theme: the brand glyph is
  white on the accent gradient (it was near-black, unreadable).

### Also in 0.10.0 — local-model knobs

- **`MEGABRAIN_CHAT_EXTRA`** — a JSON object shallow-merged into every
  OpenAI-compat chat body (streamed and not; extras win, so a knob can be
  forced). The provider-param escape hatch, born from a real wall: Ollama's
  `/v1/chat/completions` **ignores** the native `think:false`, so hybrid
  qwen3 models silently burned ~265 hidden reasoning tokens per `ask` answer;
  `'{"reasoning_effort": "none"}'` is the field Ollama honors and it pins them
  to pure instruct mode. Chat-only (never leaks into `/embeddings`), ignored
  by the claude provider, malformed JSON fails loud (a silently-dropped knob
  corrupts any measurement that relied on it).
- **`MEGABRAIN_ASK_CTX_CHARS`** — override `ask`'s candidate-prompt budget
  (default 200K chars ≈ 50K tokens, sized for cloud windows). Local models
  have smaller windows and runtimes truncate silently: golden bundles measure
  29–58K tokens vs qwen3:14b's 40960 cap, so 5/6 eval prompts were being cut
  without a trace. Cap the budget under the model's window instead.
- Fully-local stack re-measured on an RTX 3090 (docs/GUIDE.md §2b updated with
  same-day controls): jina-code Q8 GGUF ties bge-m3 on bundle_full 0.909 at
  7× less memory; qwen3-coder:30b stays the local ask pick. Lab log in
  `evals/LOCAL_MODELS.md` §(f).

## 0.9.1 — Windows: stop silently corrupting non-ASCII code · `megabrain install`

- **Fix (Windows, silent data corruption): every file read is now explicitly
  UTF-8.** The engine read source with `read_text(errors="replace")` and no
  `encoding=`, so it decoded with the *platform default* — cp1252 on Windows.
  It never raised (`errors="replace"` swallowed it), it just indexed mojibake:
  a file containing `# año` was chunked, embedded and returned as `# aÃ±o`.
  Every non-ASCII comment, string, or identifier (accents, CJK, emoji, em-dashes)
  was silently corrupted for Windows users, in the index AND in what `ask`/`query`
  handed back. All 27 read/write sites across the indexer, retrieval, forge,
  flows, strategies and the servers now pin `encoding="utf-8"` (keeping
  `errors="replace"` so a genuinely broken file still can't crash a run).
  Windows CI was red on this since 0.8.0 — it's green again.
- **`megabrain install`** registers the MCP server with every AI coding assistant

- **`megabrain install`** registers the MCP server with every AI coding assistant
  detected on the machine — **Claude Code, Codex, Antigravity, Cursor, Windsurf,
  Gemini CLI**. MCP is portable, so the same stdio server runs in all of them;
  only the config file (path/format/key) differs, and that table now lives in
  `server/install.py`. `--list` previews, `--platform` narrows, `--remove`
  unregisters. It writes **only** the `megabrain` key (other servers survive) and
  pins the entry to `sys.executable`, so re-running repairs a config that drifted
  to a stale checkout/PYTHONPATH. Codex's TOML gets a targeted section
  replace/append so comments and other servers survive (no TOML writer in the
  stdlib, and megabrain still takes no dependencies).
- **`megabrain serve` is the studio** (web UI at `/` **+** the JSON API);
  **`megabrain serve-api` is now the JSON API ONLY**, no UI mounted. Previously
  `serve-api` served both and `--no-ui` opted out — that conflated two concerns
  (the name says "api", so it shouldn't ship a UI). The `--no-ui` flag is gone;
  pick the command that matches what you want. Both share every option
  (`--port/--host/--cors/--no-llm/--token`) and drive the same `serve()`.

## 0.9.0 — MCP surface: lean, and `megabrain_query` is always signal-only

**BREAKING (MCP tool contract):**

- **Removed `megabrain_get` and `megabrain_chunks`.** Every tool costs the
  calling agent context and a routing decision, so the MCP surface now exposes
  only what megabrain alone can do — pulling a single file or symbol is the
  host's own Read/Grep job (and `ask`'s sub-agents already fetch files
  internally via their own tools). Five tools remain: `megabrain_ask`,
  `megabrain_query`, `megabrain_index`, `megabrain_forge`, `megabrain_flows`.
  The underlying `app.get`/`app.chunks` are unaffected — the CLI and serve-api
  (`/get`, `/chunks`) still use them.
- **`megabrain_query` always returns the pruned signal list now** — the
  `prune_noise` and `full` params are gone. The file-grouped bundle's RELATED
  section was a code-less map, a dead end over MCP once `get`/`chunks` were
  removed (no tool to expand it). Pruning has no such gap: every file in the
  bundle still contributes its best chunk *with code* — only the noisy chunks
  inside files are cut, so nothing relevant is lost, and one call now always
  hands the agent real code. The CLI (`query` vs `query --prune`) and HTTP API
  still expose both shapes.
- `megabrain_query`'s `compact` param now declares its default explicitly
  (`false` — code bodies included) instead of leaving it implicit.

No engine/CLI/HTTP changes — this release is MCP-contract-only.

## 0.8.1 — studio add-repo: native folder dialog + interactive file tree

- **Native OS folder picker.** Add-repo's "Browse…" opens the operating
  system's OWN folder dialog (`GET /fs/pick` → Finder on macOS via `osascript`,
  GTK/KDE on Linux, folders-only) and returns the absolute path — the one thing
  a browser will never hand over. Falls back to the manual path field on a
  headless box. (Replaces the earlier server-side HTML browser.)
- **Choose what indexes, as a tree.** The scan review is now an interactive
  file tree (lucide-style icons, expand/collapse, per-node checkboxes with an
  indeterminate state, All/None) built from the scan's new `paths`. The
  "N files will index" count updates live; excluded nodes become
  `.megabrainignore` lines applied before indexing. Auto-skipped
  (gitignore/vendored/generated) stays in a collapsible detail.
- **Tokened demo.** The studio reads `?token=` from its URL (then localStorage)
  and sends `Authorization: Bearer` on every request, so serve-api can run with
  `--token` behind a public port and a tokenized link is all you share.
- `scan()` returns `paths` (indexable rel paths, capped) for the tree.

## 0.8.0 — megabrain studio (serve-api web UI) + scan

- **megabrain studio.** `megabrain serve-api ~/repo` serves a local web UI at
  `/` (`server/ui/`, vanilla + system fonts, no CDN): search with the chunk
  heatmap, the prune signal/noise view, the multi-agent `ask` live-view, a
  providers panel (Claude SDK · OpenRouter · Ollama, auto-detected) with a
  model picker, and an **add-repo flow that scans first** then indexes behind a
  **live progress bar**. `--no-ui` for JSON-only.
- **Live provider switching + local Ollama.** `POST /providers/select
  {provider, model?}` repoints the CHAT role at Claude/OpenRouter/Ollama for
  subsequent calls (`providers.select()`, set-and-leave under a lock — never
  touches embeddings); `POST /providers/ollama/serve` starts `ollama serve`
  when the binary is present but the server is down (`providers.start_ollama()`).
  `detect()` now reports `ollama.installed` + `active.label` (the logical
  provider — a localhost chat base reads as ollama). serve-api **defaults to
  OpenRouter** when `OPENROUTER_API_KEY` is present (env or `~/.zshrc`) and the
  provider isn't explicitly pinned. The studio hides embedding models from the
  Ollama *chat* picker (they can't narrate) and hints `ollama pull` when only
  embeddings are local.
- **Re-index with a different embedding.** `POST /index/stream` accepts
  `embed_model` (+ `embed_base` for a local endpoint): sets the model so BOTH
  the re-index AND subsequent query embedding use it (a model change re-embeds
  every file). `/repos` + `/health` + the index `done` event now carry
  `embed_model` so you always know which embedding an index used. The studio's
  local embedding preset is **jina-code** (`unclemusclez/jina-embeddings-v2-
  base-code` via Ollama) — verified indexing + search on a fully-local repo.
- **Syntax highlighting** in the studio — a small dependency-free scanner
  (Python/JS/Go/Rust + a generic fallback), applied to the search chunk
  heatmap, the prune signal chunks, and the `ask` spliced code blocks.
- **New serve-api routes:** `GET /providers` (`providers.detect()`),
  `GET /scan?path=`, `GET /prune`, `GET /repos`, `POST /repos/add`,
  `POST /index/stream` (SSE per-file progress via a new `on_progress` indexer
  hook). Every route accepts an optional `?repo=`/`"repo"` (multi-repo
  registry). `POST /ask` + `/ask/stream` accept an optional `model`, threaded
  cleanly through the ask pipeline (no env mutation).
- **`scan` — index intelligence** (`indexing/ignore.py`): a stdlib `.gitignore`
  matcher + Linguist-style vendored/generated detection + a census. CLI
  `megabrain scan [--write]`, `index --scan|--dry-run`; the studio add-repo
  flow shows it before committing. Deterministic and opt-in — a plain `index`
  is byte-identical.
- **Fix:** broken relative imports from the `arch(D)` refactor
  (`docsearch.py`, `session.py`) that crashed `serve-api` at boot.

### Also shipped in 0.8.0 — the art-of-code refactor (v2)

Internal architecture pass toward the "art of code" layering. **No public
contract changed**: the CLI/MCP/serve-api surfaces, the on-disk index + trust
formats, and the Python API are byte-compatible; MCP tool schemas and chunker
output are pinned byte-for-byte by new golden tests. See `REFACTOR.md`.

- **One `ask` pipeline.** `ask()` is now a buffered collector over
  `stream_events`; the flow-cache read AND write live in that single pipeline.
  Fixes a real divergence where CLI/SSE asks never populated the flow cache.
- **Structured errors** (`megabrain/errors.py`): a `MegabrainError` taxonomy
  with `code` + `http_status`; one catch site per frontend (CLI no longer dumps
  tracebacks; HTTP no longer leaks internals).
- **`ChatProvider` Protocol + registry** (`providers/base.py`): the three
  provider if-switches collapse into `resolve()`; `agent_stream` is a probed
  capability. Mirrors the `ChunkStrategy` pattern.
- **`ChunkMeta`** (`megabrain/model.py`): the chunk row is a frozen typed
  record end-to-end; the SQL column order lives only in `store.py`.
- **`RetrievalParams`** (`retrieval/params.py`): every tuning knob in one frozen,
  injectable record (sweeps replace() it instead of monkeypatching globals).
- **`query.py` split** into `state / scoring / bundle / render / files`;
  `query.py` is now a compatibility facade. `selection()` is the single
  definition of signal (prune + chunks_for_file are projections of it).
- **`app.py` application-service layer**: one use-case per verb + the shared
  pre-steps (resolve/rel_join/agents tri-state/reindex) all frontends call.
  `docsearch.py` and `session.py` (RepoSession) extracted from the HTTP handler.
- **One cAST engine** (`chunkers/cast.py`): `merge_units` / `greedy_pack` /
  `pack_lines`, shared by the Python and tree-sitter chunkers (were duplicated).
  Byte-identity proven by `tests/test_cast_unification.py`.
- **Scoring lanes**: `score_chunks` is a self-gating `ScoreLane` pipeline
  (dense+fusion · test-penalty · issue · lexical) over one `QueryCtx` — adding a
  signal is one lane class + one entry (OCP). Bit-identical: a float-array
  differential harness (`tests/test_scoring_lanes.py`) proves no score moved.
- **src/ layout + one subpackage per layer** (PyPA standard): `storage/`
  (store + flow-cache mechanics), `ask/` (narrator · agents · warmup — the LLM
  half of flows cut out so storage never imports upward), `forge/` (coverage ·
  ab_gate · specialize), `server/` (cli · mcp · http · session),
  `retrieval/docsearch`, `__main__.py`. Package root keeps only the
  cross-cutting spine. `python3 -m megabrain.mcp_server` and the `megabrain`
  script are unchanged; `from megabrain.ask import ask` still works (package
  interface). No compatibility facades: every import names the real module.
- **Anti-shadowing guard** (`tests/test_no_shadowing.py`): an editable install
  of the old engine can silently fill in missing `megabrain.*` names via
  setuptools' meta-path finder; the guard asserts every loaded module lives
  under this repo's `src/` and retired names never resolve from here.
- **`TreeChunkerOps`** public contract for the php→tree-sitter reuse seam.
- **Lifecycle**: `SearchState`/`Store` close via context managers everywhere;
  `index_repo` owns its connection and returns stats (the library never prints).
- **Layering**: indexing no longer imports the `flows` feature module
  (stale-flow invalidation is now `Store` integrity).

## 0.7.2 — 2026-07-11

- **`prune_noise` — NO-LLM noise pruning on the query path.** A new option
  (`megabrain query --prune`, MCP `megabrain_query` `prune_noise: true`,
  `prune_search()` / `render_pruned()` in the library) that runs the normal
  retrieval and then returns ONLY the SELECTED (signal) chunks as a FLAT list
  ranked by relevance — `[id] file:lines · score` + code — with the noise
  dropped. It reuses the exact signal/noise selection the engine already
  computes (a tier-1 chunk surviving the CHUNK_KEEP_RATIO cut, or a related
  file's best chunk), so it's deterministic, has no LLM and no token cost. It is
  the lean alternative to `ask` when the caller just needs the right code to
  read, not a narration — a modern LLM needs no pre-filtered prose, so `ask` is
  deliberately left as-is (no pre-filter: that would be double work). Opt-in:
  default `megabrain_query` still returns the full file-grouped bundle.
  `prune_search(..., include_pruned=True)` also returns the dropped chunks under
  `"noise"` for a signal-vs-noise diff view (powers the demo's prune view).

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
