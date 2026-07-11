# megabrain — Architecture

> Code-intelligence engine. One call returns all the code related to a question,
> explained like a senior engineer with the real code spliced in. Built to replace
> minutes of agent file-crawling with one grounded answer.

Every load-bearing choice below is locked by experimental data (golden-set gates,
model bakeoffs — see §7). The five hard rules:

1. **No LLM in the retrieval path** — LLM pruning was tested four ways and every
   variant cost completeness or added 1–2 s for no recall gain. The only LLM calls
   are `ask` (post-retrieval narrator) and `--best` (optional reorder) — both fail-open.
2. **Completeness beats ordering** — the bundle is tuned so golden `bundle_full`
   recall is **1.00**. A change that lowers it is not merged. Noise is handled by
   *render structure* (§4.3), never by dropping files.
3. **The graph never ranks** — import/call edges supply candidates and map
   annotations only. PageRank-as-ranking was rejected (Acc@1 0.91 → 0.73).
4. **Chunks are a line partition** — every file's chunks cover every line exactly
   once, no gaps, no overlaps (`validate_partition` must stay clean).
5. **`ask` shows real code only** — the LLM cites spans; the engine splices verbatim
   code from disk. The model can never emit (hallucinate) code.

---

## 1. The pipeline at a glance

```
                 INDEX TIME (once per repo, incremental after; auto-refresh 60s TTL)
  repo files ──► chunkers (cAST) ──► embed (OpenRouter/local) ──► SQLite (.megabrain/db.sqlite)
                     │                                        chunks · vectors · symbols
                     ├─ symbols (defs/classes/consts)         file skeletons · edges
                     ├─ file skeleton (signatures)
                     └─ import/call graph edges (py · ts/js · php)

                 QUERY TIME (per question)
  question ──► retrieve (no LLM, ~10–200 ms) ──► bundle ──► [ask] narrate (1 chat call)
               dense + file-fusion + graph            splice verbatim code into [[k]] cites
               (+ issue-mode lanes for long queries)
```

Entry points share one retrieval core: CLI (`megabrain …`), MCP stdio server
(`megabrain_ask/_query/_get/_chunks/_index`), HTTP (`serve-api`), and the Python API
(`megabrain.search/ask/…`, lazy imports, `py.typed`).

| command | LLM? | latency | use |
|---------|------|---------|-----|
| `query` | no | ~10–200 ms | complete bundle: CORE full code + RELATED map (`--full` for RELATED bodies) |
| `ask`   | 1 chat call | ~6–25 s | narrated walkthrough, verbatim code spliced at each citation |
| `get` / `chunks` | no | <10 ms | one file/symbol · per-chunk scores for one file |

---

## 2. Index time

### 2.1 Chunkers (`megabrain/chunkers/`)

All chunking sits behind one contract (`chunkers/base.py`): `chunk_file(relpath,
source) -> FileResult` — chunks + symbols + skeleton, **partition-guaranteed**.

Split-then-merge over the AST (the cAST recipe, arXiv 2506.15655): walk top-level
nodes (comment/blank gaps attach to the following unit), **merge** small units up to
a budget of **4000 non-whitespace chars**, **split** oversized ones (big class →
class-header + per-method chunks; big function → `part k/n` blocks; unsplittable
giants → line windows). Every chunk carries a **breadcrumb**
(`repo > path > class Sig > def method(sig)`) that is prepended to the embedded text
(contextual retrieval).

- `python.py` — stdlib `ast`.
- `treesitter.py` — the same algorithm parameterized by a **`LangSpec`** (grammar,
  def node types, name/body fields, export unwrap): TS/TSX/JS/JSX, Ruby, Go, Rust,
  PHP. Adding a language = one LangSpec entry + `pip install tree_sitter_<lang>`
  (auto-activates when the grammar is installed).
- `php.py` — **shape-routed PHP**: modern (namespaced/PSR) files keep the generic
  chunker; legacy-2000s procedural/mixed-HTML files take a section chunker
  (standalone defs with their doc-banner attached, `//----` banners as headings,
  HTML islands, QMD scored cuts).
- `markdown.py` — no-LLM doc chunker: score candidate cut lines (H1=100…H6=50,
  code-fence boundary=80, paragraph=20) and cut at the best score near the budget,
  so chunks are heading-aligned and never split mid-section. Headings become
  symbols; the outline is the skeleton.

**Custom strategies (public extension point):** the registry contract is the
`ChunkStrategy` protocol; `index_repo(root, strategies=[MyStrategy()])` injects
caller strategies ahead of the built-ins — claim a new content type (`.sql`,
`.proto`, `.ipynb`…) or override an existing one without forking. Partition is the
only hard requirement. Runnable demo: `examples/02_custom_chunker.py`.

### 2.2 Two embedded granularities

Embeddings go through any OpenAI-compatible `/embeddings` endpoint — OpenRouter by
default (model `perplexity/pplx-embed-v1-0.6b`, 1024-d), or a local server
(Ollama/LM Studio/vLLM) via `MEGABRAIN_EMBED_BASE_URL` (keyless on localhost).
Wire detail: int8-base64 **unnormalized** vectors are decoded and L2-normalized
(float arrays handled too). A content-addressed disk cache (atomic writes) makes
re-indexing near-identical checkouts almost free; changing the embed model
auto-triggers a full re-embed on the next `index` so vectors never silently mismatch.

- **Chunk vectors** — breadcrumb + code of each chunk.
- **File skeletons** — per file, signatures + docstrings + module constants embedded
  as one vector: the file-level relevance signal for the fusion in §3.1.

### 2.3 Symbols & graph

- **Symbol table** — every def/class/method/const with qualified name, kind, line
  range, signature, doc first-line. Powers outlines, the entity-ID lexical lane and
  `get --symbol`.
- **Import/call graph** (`graph.py`) — Python: `from pkg.x import Y` + call sites to
  unique defs. TS/JS: relative imports incl. `export * from`, dynamic `import()`,
  side-effect imports. PHP: `use` statements resolved against a namespace+declaration
  FQCN index (PSR-4-agnostic). Edges feed **candidates and annotations only** (rule 3).

### 2.4 Storage, incrementality, freshness (`store.py`, `indexing/indexer.py`)

One SQLite file per repo at `<repo>/.megabrain/db.sqlite` (`chunks`, `files`,
`symbols`, `edges`, `meta`). Indexing is incremental by SHA-256; orphans are pruned
(incoming edges drop only then — re-index preserves them). Relpaths are **POSIX on
every platform** (`as_posix()`; Windows backslash keys corrupted the index once —
CI's Windows matrix is the regression guard). No daemon or watcher: CLI
`ask`/`query`/`chunks` and the MCP server **auto-refresh a stale index (60 s TTL,
fail-open without a key)** before answering, so results always match disk. Vectors
load into one NumPy matrix; brute-force cosine is <2 ms up to ~50 K chunks, so ANN
indexing is deliberately deferred.

### 2.5 forge — self-authored chunkers (`forge.py`, repo-local strategies)

`megabrain forge <repo>` closes the custom-strategy loop: the engine detects the
repo's uncovered text extensions (deterministic census — no LLM), has an LLM
(the `ask` provider stack; `MEGABRAIN_FORGE_MODEL` to pin) write a
`ChunkStrategy` from the contract source (`chunkers/base.py`, verbatim) plus
real sample files, and accepts it **only** after it chunks every matching file
in the repo with a clean `validate_partition` — failures feed a repair loop
(≤3 attempts), so unvetted code can never install. This keeps the hard rules
intact: the LLM writes code once, at forge time, gated by the partition oracle;
retrieval stays LLM-free.

Vetted modules live in `<repo>/.megabrain/strategies/<ext>.py` and load
automatically on every `index_repo` — including the 60 s auto-refresh — so
forged extensions never fall out of the index. Loading executes repo-provided
code, so it is **trust-gated**: a module only loads when its sha256 matches the
entry in the *user-level* store `~/.megabrain/trust.json` (which a cloned repo
cannot write). forge records the sha on install; `megabrain trust <repo>`
approves hand-written modules; any edit un-trusts the file (skipped with a loud
warning) until re-approved. The oracle guarantees the *current* corpus — a
future file that breaks a forged strategy surfaces in the index stats'
`partition_violations`, never silently.

Surfaces: CLI `megabrain forge [--list|--dry-run|--ext .x]` / `megabrain trust`,
MCP `megabrain_forge` (`list_only`, `dry_run`, `ext`). Real run on pallets/click:
`.toml` (11 files) + `.yaml` (8 CI workflows) both forged first-attempt in ~28 s;
"which workflow runs the test suite" went from a total miss to
`.github/workflows/tests.yaml` at #1.

**Specialization (`--specialize`, `forge_specialize.py` + `forge_eval.py`)** is
the second half: covered file types that the generic chunker splits poorly —
the detector (`detect_specialization`) diagnoses three shapes: a dominant
dict/list **table** (proven highest-yield), a **blob** (>55% of a file in one
chunk), and the **line-window** fallback. Generation fans out in parallel (one
LLM per extension-opportunity) and must produce a **shape-router**: handle the
diagnosed shape, `builtin_strategy_for(ext)` for everything else — normal files
chunk byte-identically. Partition is necessary but NOT sufficient here (a legal
chunker can be worse than the built-in), so there is a second, empirical gate:

- `forge_eval.probe_spans` derives neutral ground-truth (query, span) pairs
  from the file's own structure (python ast dict-entries/defs; generic
  blank-line blocks otherwise) — no human labels, no LLM, chunker-independent.
- `forge_eval.ab_gate` indexes built-in vs candidate for real (temp copies,
  real embeddings) and measures **rank-aware span-IoU** — the overlap of the
  file's *top-ranked* chunk with the true span, i.e. what a user actually gets
  when the file is retrieved — plus global hit@k, on EVERY file the candidate
  changes (`changed_files`), not just the diagnosed target. WIN needs all of:
  pooled IoU lift ≥ 0.01 · pooled hit@1 held · no per-file regression · no
  micro-chunking (median chunk ≥ 100 nws chars, checked before any indexing).
  A losing-but-valid candidate gets ONE regeneration seeded with the measured
  result; still losing → nothing installs.

The gate's teeth are empirical: an early best-IoU-over-all-chunks variant let
an LLM candidate "win" express with median 1-LINE chunks (perfect geometry,
useless embeddings, pooled 0.55). Rank-aware IoU + the hit@1 clause + the
granularity floor make that family of metric-gaming un-installable — re-run
under the strict gate, that candidate measures IoU Δ-0.001 with hit@1
*regressing* 0.13 → 0.07 and is rejected. Wins that survive: psf/requests
`status_codes.py` (68-entry dict, one blob under the built-in) IoU 0.010 →
0.076 with hit@1 0.23 → 0.47 (2×); sinatra `.rb` (many-short-method classes)
IoU 0.037 → 0.115 with hit@1 held and the worst touched file still +0.013 —
all other files byte-identical in both cases.

---

## 3. Query time — retrieval (no LLM)

`retrieval/query.py`. `load_state()` loads matrices once (servers keep it warm and reload on
db-mtime change); `search_with_state()` runs per query, all vectorized.

### 3.1 Scoring

```
dense_i  = cosine(query, chunk_i)                      # chunk relevance
file_i   = cosine(query, skeleton(file of chunk_i))    # file relevance
fused_i  = dense_i + 0.5 · file_i                      # dual granularity (validated)
fused_i *= 0.85   if chunk_i is in a test file         # soft test down-weight
```

The `+ 0.5 · file` term is the validated core hypothesis: a strong chunk in a weak
file shouldn't outrank a decent chunk in the clearly-relevant file. For short
developer queries (≤25 identifier tokens), small grid-tuned boosts reward exact
filename/symbol token matches.

### 3.2 Issue mode (long queries — bug reports, >25 ident tokens)

Three extra deterministic signals (no LLM), with the expensive lanes **cached on
`SearchState`** for warm servers:

- **Variant ensemble** — title / traceback / fenced-code / identifier-bag views,
  embedded in one batch call, RRF-merged (full-issue ranking double-weighted).
- **Traceback grounding** — Python `File "x.py", line N` **and** JS/TS
  `at fn (src/x.ts:12:5)` frames pin files and enclosing-function spans with tiered
  bonuses; explicit source paths (`.py/.ts/.js/.go/.rb/.rs/.php/…`) and backticked
  identifiers ground through a symbol cascade (exact → lowercase → dotted-suffix).
- **Entity-ID BM25 lane** — postings-based sparse channel over each file's path +
  symbol names + signatures, RRF-merged. Issue-mode only: it raised SWE-bench recall
  but cost golden completeness on short queries.

### 3.3 Bundle assembly + the RELATED render policy

Rank files by best chunk; take top candidates; pull **graph neighbors** of the top
files as extras. **CORE** = files within 3% of the top score → matching chunks in
full + a symbol index of the rest. **RELATED** = every other candidate.

Measured on the golden set (22 queries, 40 verified gold files):

| | bundle_full |
|---|---|
| CORE only | 0.36 |
| CORE + RELATED | **1.00** |

**45% of gold files live in RELATED — it can never be dropped** (and LLM pruning
stays rejected: every phase-5 variant lost gold files). But by *volume*, RELATED is
~17 files/query at ~5% verified gold, and its inline code bodies were ~16K of a
~22K-token render. So the fix is structural, in the **render only**: RELATED shows
**file · best-match span pointer · symbols** by default (−65% tokens, 22K → 8K);
`query --full` (MCP `full: true`) restores inline bodies; `--compact` strips all
bodies. The bundle **data** always carries `best_chunk` — ask, serve-api and the
webui consume it unchanged. Expansion is multi-turn: `megabrain get <file>
[--symbol N]`.

Optional `--best`: listwise LLM reorder (`rerank.llm_order` — 3 parallel votes,
mean-rank merge, a file can rise freely but fall ≤1 place). Permute-only, so recall
is untouched by construction. Off by default.

---

## 4. `ask` — narration with verbatim code (`ask.py`)

The LLM is a narrator that can only **point**, never paste:

1. Retrieve (§3); flatten CORE chunks + RELATED best-chunks into a numbered
   candidate list. Three content modes: **code-only (default)**, `--docs`
   (docs-only), `--with-docs` (code + docs). Candidates are capped at 200K chars —
   one call always fits.
2. **One streamed chat call**: the prompt forbids quoting code and requires
   double-bracket citations — `[[3]]` (whole chunk) or `[[3:705-731]]` (line range;
   an `L` prefix is tolerated because models mirror the prompt's `L1-172` headers).
3. **Splice**: every citation is replaced with the **verbatim block from disk**
   (real file, real line numbers; sub-ranges snap to enclosing symbol edges; repeats
   dedupe to a back-reference). The CLI streams live — prose token by token, each
   citation spliced the moment its line completes.
4. **Fail-open**: no key, no citations, or an API error → the full unfiltered bundle.
   Non-cited candidates are always listed in a footer (the filter is never silent).

### 4.1 Chat providers — Claude Code credits or OpenRouter

Chat routing (`providers.chat_provider()`) is **auto**: `claude` when
`claude_agent_sdk` is importable, else `openrouter`; pin with
`MEGABRAIN_CHAT_PROVIDER`. Models per provider via `MEGABRAIN_ASK_MODEL` /
`MEGABRAIN_RERANK_MODEL` (defaults: `haiku` on claude, `qwen/qwen3-coder` on
OpenRouter — a bakeoff found qwen on par with Haiku on citation selection at ~5×
lower cost, since retrieval already guarantees completeness).

- **`claude`** (`providers_claude.py`, extra `megabrain[claude]`) — the Claude Agent
  SDK drives the Claude Code CLI: a logged-in **subscription** narrates on Claude
  Code credits with zero keys; `ANTHROPIC_API_KEY` bills the API instead. The
  narration transport pins pure narration (no tools + an explicit disallow list + a
  no-tools preamble; without it the agent runtime sometimes tried to "search the
  codebase" and burned the turn). Streaming via partial-message events — same
  `(text, finish_reason)` contract as the SSE path. ask v2 sub-agents use a second
  transport (`agent_stream`): megabrain's retrieval tools register as an in-process
  MCP server and the SDK runs the tool loop itself — builtins stay disallowed.
- **`openrouter`** (`providers.py`, urllib-only) — any OpenAI-compatible endpoint;
  `MEGABRAIN_CHAT_BASE_URL` points it at native APIs or local servers. For ask v2,
  `stream_chat(with_tools=True)` also accumulates fragmented `delta.tool_calls`
  and the loop runs in `ask_agents`.

**Embeddings never use this switch** — Anthropic has no embeddings API, so
index/query always need OpenRouter or a local embed endpoint. The two lanes are
independent by design (hybrid local-embed + Claude-narrate works).

### 4.2 ask v2 — adaptive multi-agent synthesis (`ask_agents.py`)

Broad questions dilute a single narrator, so `ask` branches on **retrieval shape**
(no LLM, ~0ms — `classify_bundle`): several CORE files inside the tier1 gap,
candidates spread across ≥3 top-level dirs, ≥4 RELATED files near score parity, or
an issue-length query → **broad**. Scoped questions never pay the fan-out.

The fan-out (`run_agents`, gated to ≤4 sub-agents, ≤3 tool rounds each):

1. **Repo map** — every indexed path + its skeleton docline (from the file matrix
   already in `SearchState`), budget-capped; goes in EVERY agent's prompt.
2. **Plan** — one cheap LLM call (`rerank_model`) splits the question into scoped
   sub-queries and assigns each agent a slice of the shared candidate list
   (fail-open → deterministic top-level-dir clustering → single-agent ask).
3. **Parallel sub-agents** (ThreadPool, the `rerank.llm_order` pattern) — each
   knows it is "sub-agent k of n" whose answer will be synthesized, sees the repo
   map + its chunks with **GLOBAL `[[k]]` numbering**, and may call retrieval
   **tools** (`search_more` / `get_file` / `get_symbol` — the backends are
   `search_with_state`/`get_code`, so rule 1 holds: no LLM in retrieval).
4. **Synthesis** — a streamed parent call merges the partials into one walkthrough,
   preserving the global citations, so the UNCHANGED splice pipeline grounds every
   block verbatim and dedupes repeated spans.

Everything emits JSON events (`plan`, `agent_start/delta/tool/done`,
`synthesis_delta` with spliced markdown, `done`) through `stream_events` — the CLI
prints status lines, `/ask/stream` forwards them as SSE, buffered callers (MCP,
`POST /ask`) just take the final dict. Fail-open chain end to end: fan-out error →
single-agent ask → full bundle.

---

## 5. Serving surfaces

- **MCP** (`mcp_server.py`, stdio, no deps): `megabrain_ask` (primary; `docs`,
  `include_docs`, `scope_path`, `agents` — omit for auto fan-out on broad
  questions; MCP is request/response, so the fan-out runs buffered and the trace
  lands as a footer), `megabrain_query` (`compact`, `full`, `scope_path`),
  `megabrain_get`, `megabrain_chunks`, `megabrain_index`. Auto-refreshes stale
  indexes before answering.
- **HTTP** (`frontends/http.py`, stdlib `http.server`, warm state, db-mtime auto-reload):
  `/search` `/docsearch` `/chunks` `/ask` `/ask/stream` (SSE: the ask v2 event
  stream — plan, per-agent deltas/tools, spliced synthesis) `/get` `/index`
  `/health`. Optional Bearer auth (`--token` / `MEGABRAIN_API_TOKEN`) on everything
  but `/health`; `get_code` enforces repo-root containment (path-traversal
  hardened). `/docsearch` groups are per-deployment config
  (`.megabrain/docsearch.json` or env), not engine knowledge.
- **PATH-SCOPE** everywhere: pass a sub-path (`~/repo/src/auth`) and retrieval is
  confined to files under it; the repo root is auto-detected from `.megabrain` up
  the tree. Multi-repo: comma-separated roots, searched concurrently, merged by
  score.
- **Web demo** (`examples/webui/`, stdlib, one port): live file ranking → per-chunk
  heatmap (`chunks_for_file` — span, score, *selected by the real cross-file
  retrieval* flag), native folder picker, doc-mode toggle, and an **Explain** overlay
  that A/Bs the same question on Claude vs OpenRouter with per-stage timings —
  streamed over `/api/ask/stream` (SSE): one card per sub-agent appears at `plan`,
  streams its prose and tool calls live, minimizes on `agent_done`, and the
  synthesis renders below with the real code spliced in.

---

## 6. Layout

The tree mirrors the pipeline — content → index → retrieval → narration → surfaces:

```
megabrain/
  __init__.py        public API (lazy, typed)
  forge.py           self-authored chunkers (coverage): uncovered-ext census · LLM
                     generate · partition-oracle validate (repair loop) · trust install
  forge_specialize.py  specialization: diagnose poorly-chunked covered files (table/
                     blob/window) · parallel LLM shape-routers · install only on a
                     measured A/B win (gate feedback drives one regeneration)
  forge_eval.py      the empirical gate: neutral probe spans from file structure ·
                     span-IoU/hit@k on every changed file · ab_gate win/lose
  ask.py             narrated walkthrough with verbatim splice (code/docs/code+docs modes)
  ask_agents.py      ask v2: broad-query classifier · planner · parallel tool-enabled
                     sub-agents · synthesizer · the stream_events event driver
  store.py           SQLite schema + loads (close/context-manager)
  chunkers/          CONTENT → CHUNKS: base (contract) · python · treesitter+LangSpec · php · markdown
  indexing/          BUILD the index
    indexer.py         registry-driven incremental walk + maybe_reindex (60s TTL)
    strategies.py      ext → strategy registry + ChunkStrategy protocol (custom via
                       index_repo) + trust-gated repo-local loading (.megabrain/strategies)
    graph.py           import/call edges (py · ts/js · php)
  retrieval/         ANSWER queries (no LLM in this package — rule 1)
    query.py           scoring, issue mode, bundle, render (RELATED map), chunks_for_file, multi-repo
    issue.py           deterministic issue parsing (py + js/ts frames, variants)
    bm25.py            sparse entity-ID lane (postings)
    rerank.py          optional listwise LLM reorder (permute-only, --best)
  providers/         everything that talks to a model API
    __init__.py        chat routing (auto claude/openrouter) + OpenAI-compat clients + keys
    claude.py          Claude Agent SDK transport (subscription credits / ANTHROPIC_API_KEY)
    embeddings.py      embed client (int8 decode, L2 norm, atomic disk cache)
  frontends/         entry points over the same engine
    cli.py · mcp.py · http.py   (megabrain CLI · stdio MCP · serve-api)
  mcp_server.py      launcher shim — keeps `python3 -m megabrain.mcp_server` registrations working
examples/            programmatic API · custom .sql chunker · chunk heatmap · web demo
```

Public API (lazy, typed): `megabrain.{index_repo, search, render, get_code,
load_state, search_with_state, Store, ChunkStrategy, Chunk, Symbol, FileResult,
validate_partition}`; the walkthrough via `from megabrain.ask import ask,
render_ask, stream_ask`.

---

## 7. Evidence (where the numbers live)

- **Golden gate** (30 human-verified queries over a private corpus, maintainer-side):
  R@1 **0.86** · **bundle_full 1.00** · p50 ~10 ms warm. Multi-repo and 134K-line
  scale gates alongside. The offline suite (`python -m pytest`, no network/key) is
  what CI runs on 3.10–3.13 × Linux/macOS/Windows.
- **RELATED analysis** (this doc, §3.3): CORE-only bundle_full 0.36 vs 1.00 with
  RELATED; RELATED ≈ 5% verified gold by count but 45% of all gold files.
- **Embedding bakeoff**: pplx-embed-v1-0.6b beat pplx-4b, codestral-embed,
  openai-3-large and bge-m3 on code recall (`evals/embed_bakeoff.py`).
- **Ask-model bakeoff**: qwen3-coder ≈ claude-haiku on citation selection at ~5×
  lower cost (`evals/ask_bakeoff.py`).
- **SWE-bench Lite localization** (no training): retrieval-only Acc@1 ≈ 0.52 / @5 ≈
  0.83 — on par with the trained CodeRankEmbed retriever; ask-cited-files Acc@1 ≈
  0.69–0.71, in range of SWE-bench-trained SweRankEmbed-Large.
