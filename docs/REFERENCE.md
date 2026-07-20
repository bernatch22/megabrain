# Reference

Lookup tables. Learning megabrain? → **[Guide](GUIDE.md)**. Trying to do a specific
thing? → **[Recipes](RECIPES.md)**.

- [CLI](#cli) · [MCP tools](#mcp-tools) · [HTTP API](#http-api)
- [Environment variables](#environment-variables) · [Config files](#config-files)
- [Graph](#graph) · [Python API](#python-api)

---

## CLI

Every verb takes a repo path. For `search`/`ask`/`get`/`chunks`/`graph` that path may be
**any sub-path inside an indexed repo** — megabrain finds the root and scopes retrieval to
files under it. `install` and `repos` take no path (they're machine-level).

| command | what it does |
|---|---|
| `megabrain index [path]` | build/update the index — incremental by sha256 |
| `megabrain scan [path]` | census only: what WOULD index + every skip with its reason |
| `megabrain search <path> "task"` | retrieval, no LLM: CORE code + RELATED map |
| `megabrain ask <path> "question"` | narrated walkthrough with the real code spliced in |
| `megabrain graph [path]` | the repo as a knowledge graph |
| `megabrain get <path> <file>` | print one file (or one symbol) |
| `megabrain chunks <path> <file> "query"` | every chunk of one file, scored (JSON) |
| `megabrain studio [path]` | the web UI + JSON API |
| `megabrain serve-api [path]` | the same JSON API, no UI |
| `megabrain install` | register the MCP server with your assistants |
| `megabrain flows [path]` | manage the flow cache |
| `megabrain forge [path]` | write chunkers for uncovered file types |
| `megabrain trust [path]` | approve this repo's hand-written strategies |
| `megabrain repos` | every repo indexed on this machine |
| `megabrain stats [path]` | index shape: files, chunks, symbols, edges |

### Flags

| command | flag | effect |
|---|---|---|
| `index` | `--force` | re-embed every file, ignoring the sha cache (after an embed-model change) |
| | `--exclude PATTERN` | skip a dir/glob; repeatable or comma-separated |
| | `--warm-flows N` | after indexing, discover + pre-cache N workflows (default 6, costs N asks) |
| | `--scan` | print the census, then index honoring the smart filters |
| | `--dry-run` | census only — alias of `scan` |
| `scan` | `--write` | write the proposed `.megabrainignore` |
| | `--json` | machine-readable |
| `search` | `--prune` | flat, relevance-ranked **signal** chunks; noise dropped |
| | `--rerank` | + one LLM pass to drop vocabulary-only matches (implies `--prune`) |
| | `--docs` | search the indexed **docs** (markdown) instead of the code (search is code **or** docs, never a blend) |
| | `--full` | include RELATED code bodies (default renders RELATED as a map) |
| | `--compact` | drop code bodies, keep spans and scores |
| | `--json` | machine-readable |
| `ask` | `--docs` | explain markdown instead of code |
| | `--with-docs` | explain code **and** docs |
| | `--agents` / `--no-agents` | force / forbid the multi-agent fan-out (default: auto) |
| | `--no-map` | omit the "not cited" footer |
| `graph` | `--node FILE_OR_CONCEPT` | one node in depth — concepts resolve by embedding |
| | `--path SRC DST` | BFS route between two files/concepts |
| | `--no-labels` | skip the cached LLM community labels — fully offline |
| | `--json` | machine-readable |
| `get` | `--symbol NAME` | just that symbol |
| `flows` | `--warm N` | discover + pre-cache N workflows (default 6) |
| | `--refresh` | re-ask stale flows against the current code |
| | `--clear` | drop every cached flow |
| | `--enable` / `--disable` | opt this repo in/out |
| `forge` | `--ext .x` | one extension only |
| | `--list` | detection census, no LLM |
| | `--dry-run` | generate + validate, don't install |
| | `--specialize` | census of poorly-chunked **covered** types (measure-only) |
| `install` | `--platform NAME` | only that assistant |
| | `--list` | show what's detected, change nothing |
| | `--remove` | unregister |
| `studio` / `serve-api` | `--port N` · `--host H` | default `2134` · `127.0.0.1` |
| | `--cors ORIGIN` | allow a cross-origin browser client |
| | `--token T` | require `Authorization: Bearer T` (default `$MEGABRAIN_API_TOKEN`) |
| | `--no-llm` | disable `/ask` |
| | `--readonly` | 403 every mutating/config route ([recipe](RECIPES.md#run-a-public-read-only-demo)) |
| | `--rate-limit N` | at most N LLM asks per hour per IP |
| | `--trust-proxy` | read the client IP from `X-Forwarded-For` |

Multi-repo (`~/a,~/b`) works on `index` and `search`.

---

## MCP tools

Register with `megabrain install`, or by hand:
`claude mcp add megabrain -- python3 -m megabrain.mcp_server`.

Every tool takes `repo_path` (any sub-path works — the root is auto-detected) and
auto-refreshes a stale index before answering.

| tool | returns | parameters |
|---|---|---|
| **`megabrain_ask`** | A narrated walkthrough of the whole relevant flow with the real code spliced in verbatim. Broad questions fan out into parallel sub-agents. ~6–19 s (fan-out to ~40 s). | `question` *(req)* · `scope_path` · `docs` · `include_docs` · `agents` (`true`/`false`; omit = auto) |
| **`megabrain_search`** | The same retrieval, no LLM in the core (~200 ms): a flat ranked list of the chunks worth reading, with code, noise dropped. | `task` *(req)* · `scope_path` · `compact` · `docs` · `rerank` *(default `true`)* |
| **`megabrain_graph`** | The repo as a knowledge graph. | `mode` (`map` default · `node` · `path`) · `node` · `source` + `target` · `scope_path` |
| **`megabrain_index`** | Index/update a repo — or the registry of every indexed repo on this machine. | `repo_path` · `list` (`true` → the registry) |
| **`megabrain_forge`** | Teach megabrain a file type it can't index yet. | `ext` · `list_only` · `dry_run` · `specialize` |
| **`megabrain_flows`** | Manage the flow cache. | `action` (`list` · `get` · `delete` · `warm` · `refresh` · `enable` · `disable`) · `id` (for get/delete) · `n` (for warm) |

`megabrain_query` remains a deprecated dispatch alias for `megabrain_search`.

---

## HTTP API

Served by both `megabrain studio` (with the UI at `/`) and `megabrain serve-api`
(headless). Every route accepts an optional `?repo=` / `"repo"` — absent means the boot
repo.

| route | returns |
|---|---|
| `GET /health` | `{ok, repo, files, chunks, embed_model, uptime}` |
| `GET /config` | `{readonly, rate_limit, version}` — what kind of server this is |
| `GET /repos` | warm sessions (`loaded: true`) + registry repos (`loaded: false`) |
| `GET /providers` | provider detection for the settings panel |
| `GET /scan?path=` | the add-repo census |
| `GET /get?file=&symbol=` | one file's real code |
| `GET /symbols?file=` | a file's outline — no `file` = the repo-wide name index |
| `GET /symbol?name=` | repo-wide definitions of a bare name (go-to-definition) |
| `GET /chunks?file=&q=` | every chunk of one file, scored + `selected` |
| `GET /prune?q=&rerank=&docs=` | the flat signal list (`rerank=1` adds the LLM lane; `docs=1` searches the docs only) |
| `GET /graph?mode=&node=&source=&target=` | the knowledge graph |
| `GET /flows` | the flow cache listed (id · question · files · created · stale) |
| `GET /flow?id=` | one cached flow in full |
| `GET /queries` | starter questions (`{source, queries}`) |
| `GET /docsearch?q=` | docs-site search projection |
| `GET /fs/pick` | open the host's native folder dialog |
| `POST /search {query}` | the raw CORE/RELATED bundle |
| `POST /ask {question, model?, agents?}` | buffered narrated answer |
| `POST /ask/stream` | the multi-agent live view (SSE) |
| `POST /index {force?}` · `POST /index/stream` | (re)index, blocking or per-file SSE |
| `POST /repos/add {path, ignore?}` | register + load a repo |
| `POST /flows/delete {id}` | drop one cached flow |
| `POST /providers/select` · `POST /providers/ollama/serve` | switch / start a provider |

`--readonly` refuses `/scan`, `/fs/pick`, `/index`, `/index/stream`, `/repos/add`,
`/providers/select`, `/providers/ollama/serve` and `/flows/delete` with a 403.
`--token` exempts only `/health`, `/config` and the UI.

**SSE events** (`/ask/stream`): `retrieval` · `cached` · `classified` · `planning` ·
`plan` · `agent_start` · `agent_delta` · `agent_tool` · `agent_done` · `agent_error` ·
`synthesis_start` · `synthesis_delta` · `length` · `bundle` · `error` · **`done`**.
`done` terminates the stream on **every** path — a sink never has to know which branch
answered to know the answer ended.

---

## Environment variables

| variable | default | what it does |
|---|---|---|
| `OPENROUTER_API_KEY` | — | the one key for embeddings + narration (read from env or `~/.zshrc`) |
| `ANTHROPIC_API_KEY` | — | bill the Claude API instead of Claude Code credits |
| `PERPLEXITY_API_KEY` | — | auto-picked when the embed base URL is `api.perplexity.ai` |
| `MEGABRAIN_EMBED_MODEL` | `perplexity/pplx-embed-v1-0.6b` | the embedding model |
| `MEGABRAIN_EMBED_BASE_URL` | OpenRouter | any OpenAI-compatible endpoint (localhost needs no key) |
| `MEGABRAIN_EMBED_API_KEY` | — | key for a non-OpenRouter embed endpoint |
| `MEGABRAIN_EMBED_DIMS` | inferred | assert the expected dimensionality |
| `MEGABRAIN_EMBED_BATCH` | — | shrink request size for local servers |
| `MEGABRAIN_ASK_MODEL` | `google/gemini-3.1-flash-lite-preview` · `haiku` on Claude | the narration model |
| `MEGABRAIN_RERANK_MODEL` | the ask model | model for `search --rerank` / MCP rerank |
| `MEGABRAIN_FORGE_MODEL` | the ask model | model that writes forged chunkers |
| `MEGABRAIN_CHAT_PROVIDER` | auto | pin `claude` or `openrouter` (auto = claude when its SDK is importable) |
| `MEGABRAIN_CHAT_BASE_URL` | OpenRouter | point chat at a native API or a local server |
| `MEGABRAIN_CHAT_API_KEY` | — | key for a non-OpenRouter chat endpoint |
| `MEGABRAIN_CHAT_EXTRA` | — | JSON merged into every chat request (e.g. `{"reasoning_effort":"none"}`) |
| `MEGABRAIN_ASK_CTX_CHARS` | `200000` | `ask`'s candidate budget — **lower it for local models** |
| `MEGABRAIN_FLOW_CACHE` | on | `0` kills the flow cache everywhere (beats a per-repo enable) |
| `MEGABRAIN_REGISTRY` | `~/.megabrain/registry.json` | override the machine-global repo registry |
| `MEGABRAIN_API_TOKEN` | — | default for `studio`/`serve-api` `--token` |
| `MEGABRAIN_DEBUG` | — | `1` re-raises engine errors with a full traceback |

Changing the embed model auto-triggers a full re-embed on the next `index`, so vectors can
never silently mismatch.

---

## Config files

| path | what it is |
|---|---|
| `<repo>/.megabrain/db.sqlite` | **the whole index** — chunks, vectors, symbols, edges, flows |
| `<repo>/.megabrainignore` | patterns to skip, one per line. Like `.gitignore` but **no `!` negation** |
| `<repo>/.megabrainqueries` | starter questions, one per line, `#` comments — drives the studio chips and seeds `flows --warm` |
| `<repo>/.megabrain/strategies/*.py` | repo-local chunkers (forged or hand-written), loaded on every index |
| `~/.megabrain/registry.json` | every repo indexed on this machine; self-heals when an index vanishes |
| `~/.megabrain/trust.json` | sha of each approved repo-local strategy — an edit un-trusts the file |

Deleting an index: `rm -rf <repo>/.megabrain`. There is no command for it, on purpose.

---

## Graph

| knob | default | what it does |
|---|---|---|
| `SEM_EDGE_MIN` | `0.80` | min cosine for a dashed semantic edge |
| `SEM_TOP_K` | `3` | semantic edges per file — keeps the map sparse |
| `SEM_WEIGHT` | `0.5` | weight of a semantic edge in label propagation (structural = 1.0) |
| `SURPRISE_MIN` | `0.85` | min cosine for a "surprising connection" |
| `--no-labels` | off | skip the cached LLM community-labelling call |

Structural edges are extracted for **Python · TS/JS · Ruby · Go · PHP**. Rust indexes
without a graph. Communities come from deterministic weighted label propagation (numpy
only) — same input, same output, every run.

---

## Python API

```python
from megabrain import index_repo, load_state, search_with_state, prune_search
from megabrain.ask import ask, render_ask
```

| name | purpose |
|---|---|
| `index_repo(root, *, force, exclude, strategies, scan_filters)` | build/update an index, returns stats |
| `load_state(root)` → `SearchState` | load the matrices once, query many times |
| `search_with_state(state, query, *, path_filter)` | the bundle, warm |
| `search(root, query)` | one-shot bundle |
| `prune_search(state, query, *, with_text, include_pruned, only_docs, exclude_docs)` | flat ranked signal chunks |
| `prune_search_root(root, query, …)` | one-shot prune |
| `render(res)` · `render_pruned(res)` | bundle → markdown |
| `get_code(root, relpath, symbol=None)` | one file or symbol (path-traversal hardened) |
| `ask(root, question, …)` · `render_ask(out)` | narrate + splice |
| `Store` · `ChunkMeta` | the storage layer and the read-side chunk record |
| `ChunkStrategy` · `Chunk` · `Symbol` · `FileResult` · `validate_partition` | the custom-chunker contract |
| `MegabrainError` · `IndexNotFound` · `EmptyIndex` · `MissingAPIKey` · `ProviderError` | the error taxonomy |

Imports are lazy and the package is `py.typed`. A custom chunker only has to satisfy one
hard rule: its chunks must form an **exact line partition** of the file.
