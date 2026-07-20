# megabrain — Architecture

> Code-intelligence engine. One call returns all the code related to a question,
> explained like a senior engineer with the real code spliced in. Built to replace
> minutes of agent file-crawling with one grounded answer.

Every load-bearing choice below is locked by experimental data (golden-set gates,
model bakeoffs — see §8). The five hard rules:

1. **No LLM in the retrieval path** — LLM pruning was tested four ways and every
   variant cost completeness or added 1–2 s for no recall gain. The only LLM calls
   is `ask` (the post-retrieval narrator) — fail-open.
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
(`megabrain_ask/_search/_graph/_index/_forge/_flows`), HTTP (`serve-api`), and the Python
API (`megabrain.search/ask/…`, lazy imports, `py.typed`).

| command | LLM? | latency | use |
|---------|------|---------|-----|
| `search` | no | ~10–200 ms | complete bundle: CORE full code + RELATED map (`--full` for RELATED bodies) |
| `search --prune` | no | ~10–200 ms | flat, relevance-ranked **signal** chunks only (noise dropped) — the existing selection, projected flat |
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
only hard requirement. Runnable demo: the megabrain-examples repo.

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

### 2.4 Storage, incrementality, freshness (`storage/store.py`, `indexing/indexer.py`)

One SQLite file per repo at `<repo>/.megabrain/db.sqlite` (`chunks`, `files`,
`symbols`, `edges`, `meta`). Indexing is incremental by SHA-256; orphans are pruned
(incoming edges drop only then — re-index preserves them). Relpaths are **POSIX on
every platform** (`as_posix()`; Windows backslash keys corrupted the index once —
CI's Windows matrix is the regression guard). No daemon or watcher: CLI
`ask`/`search`/`chunks` and the MCP server **auto-refresh a stale index (60 s TTL,
fail-open without a key)** before answering, so results always match disk. Vectors
load into one NumPy matrix; brute-force cosine is <2 ms up to ~50 K chunks, so ANN
indexing is deliberately deferred.

### 2.5 forge — self-authored chunkers (`forge/`, repo-local strategies)

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

**Specialization (`--specialize`, `forge_specialize.py` + `forge_eval.py`) is a
measure-only toolkit — NO LLM.** For a covered file type the generic chunker
splits poorly, a human writes a `ChunkStrategy` and the engine decides whether
it earns a place. `detect_specialization` diagnoses three shapes (dominant
dict/list **table**, **blob** >55% of a file in one chunk, **line-window**
fallback); `gate_strategy(root, source, ext)` measures the hand-written
candidate against a literature-tuned baseline (`lit_baseline`: the AST chunker
re-budgeted to 2000, arxiv 2605.04763) and installs it trust-gated only on a
measured win. The gate (`forge_eval`):

- `probe_spans` derives neutral (query, span) pairs from the file's own
  structure (python ast dict-entries/defs; generic blank-line blocks otherwise)
  — no labels, no LLM, chunker-independent.
- `ab_gate` indexes baseline vs candidate for real and measures **rank-aware
  span-IoU** (the file's *top-ranked* chunk vs the true span — what retrieval
  actually surfaces) + global hit@k on EVERY file the candidate changes. WIN
  needs pooled IoU lift ≥ 0.01 · hit@1 held · no per-file regression · no
  micro-chunking (median chunk ≥ 100 nws, checked before indexing). The
  granularity floor + rank-aware IoU exist because an early best-IoU-over-all-
  chunks metric let a median-1-line micro-chunker score a fake pooled 0.55.

**Why no LLM.** It used to generate these; across sinatra, requests, sdk-server
and the engine itself the generated chunkers LOST to the deterministic
lit-2000 recipe and to the default, so the path was removed. The deeper,
load-bearing finding: on the sdk-server golden (the only human-verified query
set) **no chunk budget beats 4000** — R@1 4000=0.86 · 2000=0.82 · surgical
blob-split=0.77. Tighter chunks lift span-IoU (navigation — less to read) but
LOWER retrieval ranking, because the 4000 merge concentrates a file's evidence
and that is what wins R@1. So `DEFAULT_BUDGET=4000` is a genuine optimum;
specialization is an honest win only for its navigation objective, on the rare
pathological file (a lit-2000 chunker on sinatra's many-method classes lifted
span-IoU 0.037 → 0.115 with hit@1 held). Do not chase it on ordinary code.

---

## 3. Query time — retrieval (no LLM)

`retrieval/` (scoring in `scoring.py`, assembly in `bundle.py`, exposed through
`app.py`'s use-case layer). `load_state()` (in `state.py`) loads matrices once (servers keep it warm and reload on
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
`search --full` (MCP `full: true`) restores inline bodies; `--compact` strips all
bodies. The bundle **data** always carries `best_chunk` — ask, serve-api and the
webui consume it unchanged. Expansion is multi-turn: `megabrain get <file>
[--symbol N]`.

**Noise pruning (`prune_search`, no LLM).** The bundle already marks which
chunks are *signal* — a tier-1 chunk that survives the `CHUNK_KEEP_RATIO` cut, or a
related file's best chunk. `prune_search` (CLI `search --prune`, and the ONLY shape
`megabrain_search` returns over MCP) simply **projects that existing selection into a flat list
ranked by relevance** — each `[id] file:Lstart-end · score` with its code, the noise
chunks dropped. No new scoring, no LLM, no token cost: it reuses the same
signal/noise call the full bundle makes, just rendering the signal alone (with
`include_pruned` it also returns the dropped `noise` for a signal-vs-noise diff). It
is the lean read-path answer for a coding agent that wants only the code worth
reading, not a narration; a plain `search` still returns the full CORE+RELATED bundle.

**LLM rerank (`retrieval/rerank.py`, the `llm_rerank` lane, layered ON the prune).**
The deterministic prune is recall-safe by design — every bundle file contributes its
best chunk — so files that merely *share vocabulary* with the query (tests, eval
scripts, A/B gates) survive as "signal" and bloat the output; cosine can't tell
"implements scoring" from "tests scoring". This optional lane fixes exactly that: one
buffered LLM call sees a COMPACT view of the pruned candidates (ids + spans + names +
a one-line hint, no bodies, ~2K tokens) and returns only the relevant ids, ordered.
The engine then keeps/reorders its **own verbatim chunks** and moves the dropped ones
to `noise` — the model *selects*, it never writes code (the same anti-hallucination
stance as ask's splice). It does **not** touch the deterministic scoring or ranking
(rule 1's core stays LLM-free); it is a post-retrieval selector, fail-open in every
branch (no key, timeout, malformed reply, unknown ids → the deterministic result is
returned untouched — the LLM is an optimization, never a dependency). Opt-in on the
CLI (`search --rerank`, which implies `--prune`); **default-on over MCP**
(`megabrain_search rerank: true`) and via `GET /prune?rerank=1`. Model:
`MEGABRAIN_RERANK_MODEL`, falling back to `ask_model()`. Measured on this repo's
scoring query: 21 signal chunks → 6.

### 3.4 Flow cache — self-caching workflow retrieval (`flows.py`, on by default)

**ON by default (since 0.11)** — a repo opts out with `megabrain flows
--disable` (persisted in the index meta; meta absent = on, so existing indexes
flip on without a re-index), and env `MEGABRAIN_FLOW_CACHE=0` is the global
kill that beats even a per-repo enable. When off, `load_state` skips flows
entirely and `search`/`ask` are byte-for-byte the prior behavior at zero cost.
When on — the default:

Every successful `ask` synthesizes a cross-file WORKFLOW ("VAD detects speech →
`TurnController.on_vad_start` → cancel TTS") that used to be thrown away. It is
now cached in the index and the next related question retrieves the whole flow
at once — validated: a barge-in flow cached from one question was retrieved by a
fully re-worded paraphrase. The hard rules stay intact by construction:

- **Write path (ask time)** — the RENDERED walkthrough (prose + the real code
  blocks) goes into the `flows` table with `{cited file: sha}`, and **two**
  vectors are embedded in ONE call: question + prose (the ATTACH lane) and
  question-only (the SERVE lane — so prose length can never dilute an identical
  question). "Prose" means `strip_code`, which removes fenced code, `[[k]]`
  citations **and the rendered citation headers** (`` **`src/x.py` L58-83** — sym ``).
  That last one is not cosmetic: the stored answer is what a later narrator
  reads as context, and a model shown a worked example of its own OUTPUT format
  imitates it — emitting headers instead of `[[k]]`, so the splicer replaces
  nothing and the answer names real files and line numbers with **no code
  behind them** (observed live: eight such headers, zero code). Near-duplicate
  *questions* (cos > 0.92) replace the old row. Fail-open: a cache error never
  breaks ask.
- **Read path (query time, rule 1 intact)** — pure cosine of the
  ALREADY-computed query vector against the flow matrix, in two lanes:
  - **SERVE** (`qscore` ≥ 0.88 on the question-only vector) → the cached answer
    is returned **verbatim, no LLM, ~0 ms** — but only after two guards. The
    shas of every cited file must still match DISK, *and* the cached question
    must **cover** the query (`flows.covers`): nearly every content word of the
    query has to already appear in it. That second guard exists because cosine
    is **symmetric** while "may I reuse this answer?" is not — a compound
    question that CONTAINS a cached one scores ~1.0 against it, so
    *"How do before and after filters run around a handler, **and how is a route
    defined?**"* was served the cached filters walkthrough alone, silently
    dropping the routing half (reported live on sinatra, where both halves were
    cached separately). New content words mean the caller asked for more than
    the cache holds, so the flow falls through to ATTACH and the narrator
    answers the whole question. Question scaffolding ("how does…", "where
    is…") is stopworded out, so it never decides coverage; a re-ask, a light
    rewording, and a query *narrower* than the cached one all still serve.
  - **ATTACH** (0.62 ≤ score < serve, top 2) → the flow becomes a "KNOWN FLOW"
    bundle section + non-citable context for the narrator, which narrates fresh
    and re-caches. Flows never rank or displace files (rule-3 analog) — their
    source files append to RELATED only when missing, pure additions, so
    bundle_full can only rise.
- **Invalidation (index time)** — `index_repo` prunes any flow whose cited
  files changed sha, so a stale walkthrough cannot outlive the code it
  describes. And `ask` splices real code from disk regardless: a stale flow
  could only mis-prioritize, never fabricate (rule 5 untouched).

**Warmup (explicit, costs LLM):** `megabrain index --warm-flows N` / `flows --warm N` — right
after the first index, an index-time LLM planner reads the graph's hub files (top
edge-degree) + their doclines and writes N research questions covering the main
workflows, then runs one `ask` each, so the cache starts full on day one instead
of building up lazily. Fail-open to deterministic template questions if the
planner errors. CLI `megabrain flows <repo> [--enable|--disable|--warm N|--clear]`
· kill switch `MEGABRAIN_FLOW_CACHE=0`. Related: Knowledge Compression via
Question Generation (arxiv 2506.13778).

**Inspection & onboarding:** the cache is listable everywhere — CLI
`megabrain flows`, MCP `megabrain_flows` (`action=list|get|delete|warm|
refresh|enable|disable`; `get` hands an agent a cached walkthrough for free —
no LLM, no retrieval), HTTP `GET /flows` (list) / `GET /flow?id=` (the stored
walkthrough) / `POST /flows/delete`, and the studio's **Flows tab** (list +
viewer, cited files openable in the navigator, stale marked). All of them go
through the same `app.flows_list/flow_get/flow_delete` use-cases, so no
surface can drift. **Staleness is measured against DISK** (`files_current`,
shared with the serve path), not the index's shas — the index may lag disk by
the 60 s TTL, and a flow whose sources are untouched stays serveable through
that window. `Store.stale_flows()` keeps the index comparison, which is the
right question for the *pruning* path. The Ask
surfaces show the cache working: a verbatim serve is bannered
"⚡ served from flow cache"; attached flows show as "known flows" chips (the
`retrieval` stream event carries them). A repo can commit **starter queries**
at `<root>/.megabrainqueries` (one per line, `#` comments; `GET /queries`):
the studio renders them as one-click chips in Ask with an explicit **Warm
all** button — the newcomer flow: open the repo, click through the starters,
see the main workflows, and leave them cached for everyone.

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
`MEGABRAIN_CHAT_PROVIDER`. The narrator model per provider via `MEGABRAIN_ASK_MODEL` (defaults: `haiku` on
claude, `qwen/qwen3-coder` on
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
2. **Plan** — one cheap LLM call (the ask model) splits the question into scoped
   sub-queries and assigns each agent a slice of the shared candidate list
   (fail-open → deterministic top-level-dir clustering → single-agent ask).
3. **Parallel sub-agents** (a ThreadPool over the slices) — each
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
  lands as a footer), `megabrain_search` (`scope_path`, `compact` — ALWAYS the flat
  signal-only chunk list with code; the file-grouped bundle is deliberately
  not exposed, since its code-less RELATED map is a dead end without a get/chunks
  tool, while pruning still keeps every bundle file; the **LLM rerank runs by default**
  here — `rerank: true`, fail-open — `megabrain_query` stays as a deprecated dispatch
  alias),
  `megabrain_graph` (`mode=map|node|path`, `node`/`source`/`target`/`scope_path` — the
  knowledge graph over §6, node mode splicing the file's real chunks),
  `megabrain_index` (index a repo, or `list: true` / no `repo_path` → the machine-global
  registry of every indexed repo), `megabrain_forge`, `megabrain_flows`. A tool costs the
  caller context + a routing decision, so the MCP surface carries
  only what megabrain alone can do (single-file/symbol fetches are the host's
  Read/Grep job; `ask`'s sub-agents fetch files internally). Auto-refreshes stale
  indexes before answering.
- **HTTP** (`frontends/http.py`, stdlib `http.server`, warm state, db-mtime auto-reload):
  `/search` `/docsearch` `/chunks` `/ask` `/ask/stream` (SSE: the ask v2 event
  stream — plan, per-agent deltas/tools, spliced synthesis) `/prune` (`?rerank=1` runs
  the §3.3 LLM rerank over the signal chunks) `/graph` (`?mode=&node=&source=&target=` —
  the §6 knowledge graph) `/get` `/index` (`/index/stream` SSE per-file progress)
  `/repos` (this server's warm repos **merged with the machine-global registry** —
  registered-elsewhere repos come back `loaded: false` so the studio can load them on
  click) `/providers` `/health`. Optional Bearer auth (`--token` / `MEGABRAIN_API_TOKEN`)
  on everything but `/health`; `get_code` enforces repo-root containment (path-traversal
  hardened). `/docsearch` groups are per-deployment config
  (`.megabrain/docsearch.json` or env), not engine knowledge.
- **PATH-SCOPE** everywhere: pass a sub-path (`~/repo/src/auth`) and retrieval is
  confined to files under it; the repo root is auto-detected from `.megabrain` up
  the tree. Multi-repo: comma-separated roots, searched concurrently, merged by
  score.
- **Web demo** (the megabrain-examples repo, stdlib, one port): live file ranking → per-chunk
  heatmap (`chunks_for_file` — span, score, *selected by the real cross-file
  retrieval* flag), native folder picker, doc-mode toggle, and an **Explain** overlay
  that A/Bs the same question on Claude vs OpenRouter with per-stage timings —
  streamed over `/api/ask/stream` (SSE): one card per sub-agent appears at `plan`,
  streams its prose and tool calls live, minimizes on `agent_done`, and the
  synthesis renders below with the real code spliced in.

---

## 6. Graph — the repo as a knowledge graph (`megabrain/graph.py`, numpy-only)

Where a tool like graphify spins up LLM sub-agents to *extract* relationships, megabrain
already owns them: the AST import/call edges (the `edges` table from §2.3) are the
**structural lane**, and the per-file skeleton embeddings (§2.2) add a **semantic lane**
(cosine — files that talk about the same thing without importing each other). No networkx,
no new store: it reads what indexing already produced (`Store.all_edges()` +
`Store.file_chunks()` were added for it) and runs pure numpy over it.

- **Semantic edges** — skeleton-vector cosine, top-3 twins per file above `SEM_EDGE_MIN
  = 0.80`, capped to keep the graph sparse (`SEM_TOP_K = 3`).
- **Communities** — deterministic weighted **label propagation** (numpy): structural edges
  weight 1.0 per kind, semantic edges `SEM_WEIGHT = 0.5 · cosine`; fixed ascending visit
  order + smallest-label tie-break → byte-stable across runs, renumbered by size. **No
  PageRank:** PageRank-as-*ranking* was rejected by experiment (rule 3, Acc@1 0.91 → 0.73),
  but that verdict is about ranking; communities are STRUCTURE, a different use, and label
  prop is parameter-free.
- **God nodes** — the highest structural-degree files, the repo's core abstractions.
- **Surprises** — pairs with cosine ≥ `SURPRISE_MIN = 0.85`, **no** structural edge, in
  **different** communities: the connection you didn't know was there, scored honestly.
- **Paths** — BFS between two nodes over the combined graph, each hop labelled by what
  carries it (an edge kind, or `semantic 0.87`). Endpoints resolve by **embedding**: a
  concept ("the scoring pipeline") finds its file, not just an exact path match.

The **only** LLM touch is community *labeling* — one buffered call names each community
in 2–4 words, cached in the store's `meta` table under a graph fingerprint (files + edge
counts + thresholds), fail-open to "Community N" (and `--no-labels` / offline skips it
entirely). Everything else is deterministic. `mode=node` splices the file's REAL chunks —
the graph never paraphrases code (rule 5 holds here too).

Surfaces: CLI `megabrain graph [path] [--node F] [--path A B] [--no-labels] [--json]`,
MCP `megabrain_graph(repo_path, mode=map|node|path, node?, source?, target?, scope_path?)`,
HTTP `GET /graph?mode=&node=&source=&target=&repo=`, and the studio's force-directed
canvas (§5). Measured: this repo 122 files / 324 links in ~8 ms; graphify 630 files in
~37 ms.

---

## 7. Layout

The tree mirrors the pipeline — content → index → retrieval → narration →
surfaces — one subpackage per layer (src/ layout, PyPA standard). Loose files
at the package root are only the cross-cutting spine:

```
src/megabrain/
  __init__.py        public API (lazy, typed)
  __main__.py        python -m megabrain == the megabrain script
  errors.py          structured error taxonomy (MegabrainError → code + http_status)
  model.py           ChunkMeta — the frozen read-side chunk record
  app.py             application-service layer: one use-case per verb + the
                     shared pre-steps (resolve_scope · rel_join · normalize_agents
                     · reindex policy) every surface calls
  graph.py           KNOWLEDGE GRAPH (§6): structural (AST edges) + semantic
                     (skeleton cosine) lanes · label-prop communities · god
                     nodes · surprises · embedding-resolved BFS paths · one
                     cached LLM community-label call (numpy only, no networkx)
  mcp_server.py      launcher shim — keeps `python3 -m megabrain.mcp_server` registrations working

  chunkers/          CONTENT → CHUNKS: base (contract) · cast (the ONE cAST
                     engine) · python · treesitter+LangSpec (TreeChunkerOps) · php · markdown
  indexing/          BUILD the index
    indexer.py         registry-driven incremental walk + maybe_reindex (60s TTL);
                       returns stats (never prints)
    strategies.py      ext → strategy registry + ChunkStrategy protocol (custom via
                       index_repo) + trust-gated repo-local loading (.megabrain/strategies)
    graph.py           import/call edges (py · ts/js · php)
  storage/           PERSISTENCE
    store.py           SQLite schema + loads + row packing + flow integrity
                       (+ all_edges / file_chunks — the graph's structural read)
    registry.py        machine-global repo registry (~/.megabrain/registry.json,
                       env MEGABRAIN_REGISTRY): every index registers here so any
                       frontend lists EVERY indexed repo · fail-open · self-heals
                       (drops entries whose db.sqlite is gone)
    flows.py           flow-cache MECHANICS (write/dedupe · cosine read · verbatim
                       serve · sha invalidation — no LLM; retrieval may import this)
  retrieval/         ANSWER queries (no LLM in this package — rule 1)
    params.py          RetrievalParams — every tuning knob, frozen + injectable
    state.py           SearchState + load_state (warm state, lifecycle)
    scoring.py         score_chunks — self-gating lane pipeline (dense+fusion ·
                       test-penalty · issue · lexical); add a signal = 1 lane + 1 entry
    bundle.py          rank + tier (CORE/RELATED) · selection · prune · chunks_for_file · multi
    render.py          bundle → markdown (pure view)
    files.py           get_code — the file-serving containment boundary
    docsearch.py       docs-site search projection
    issue.py           deterministic issue parsing (py + js/ts frames, variants)
    bm25.py            sparse entity-ID lane (postings)
    rerank.py          OPTIONAL post-prune LLM rerank (§3.3): one buffered call
                       over a compact candidate view drops vocabulary-only hits +
                       reorders verbatim chunks · fail-open · MCP default-on
  ask/               NARRATE (the only layer that talks to an LLM at query time)
    narrator.py        walkthrough with verbatim splice (code/docs/code+docs modes)
    agents.py          ask v2: classifier · planner · parallel tool-enabled
                       sub-agents · synthesizer · the stream_events event driver
    warmup.py          flow-cache warm/refresh orchestration (the LLM half cut
                       out of flows so storage never imports upward)
  forge/             STRATEGY GENERATION & MEASUREMENT
    coverage.py        uncovered-ext census · LLM generate · partition-oracle
                       validate (repair loop) · trust install
    ab_gate.py         the empirical gate: neutral probes · rank-aware
                       span-IoU/hit@k · champion-vs-challenger win/lose
    specialize.py      hand-written specialization, installed only on a measured win
  providers/         everything that talks to a model API
    base.py            ChatProvider Protocol (available/chat_text/stream_chat/agent_stream)
    __init__.py        provider registry + resolve() (auto claude/openrouter) + OpenAI-compat clients + keys
    claude.py          Claude Agent SDK transport (subscription credits / ANTHROPIC_API_KEY)
    embeddings.py      embed client (construction-time config; int8 decode, L2 norm, atomic disk cache)
  server/            SURFACES — thin adapters over app.py (map args → use-case → render)
    cli.py · mcp.py · http.py · session.py (RepoSession warm state, shared)
```

Runnable examples (programmatic API · custom .sql chunker · chunk heatmap ·
web demo) live in their own repo, `~/megabrain-examples` — they need the engine
installed (`pip install megabrain`).

Public API (lazy, typed): `megabrain.{index_repo, search, render, get_code,
load_state, search_with_state, prune_search, prune_search_root, render_pruned,
Store, ChunkMeta, ChunkStrategy, Chunk, Symbol, FileResult, validate_partition,
MegabrainError, IndexNotFound, EmptyIndex, MissingAPIKey, ProviderError}`; the
walkthrough via `from megabrain.ask import ask, render_ask, stream_ask`.
`prune_search(state, query, path_filter=None, with_text=True,
include_pruned=False)` returns `{query, repo, chunks:[{id, file, start_line,
end_line, kind, name, score, text}], kept, pruned, scanned, ms}` (with
`include_pruned=True`, also `noise:[...]`); `prune_search_root(root, query, …)` is
the one-shot entry.

---

## 8. Evidence (where the numbers live)

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
