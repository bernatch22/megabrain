<p align="center">
  <img src="https://raw.githubusercontent.com/bernatch22/megabrain/master/assets/megabrain.png" alt="megabrain" width="180">
</p>

<h1 align="center">megabrain</h1>

<p align="center">
  <b>One call returns all the code related to a question</b><br>
  — explained like a senior engineer, with the real code spliced in.
</p>

<p align="center">
  <a href="https://pypi.org/project/megabrain/"><img src="https://img.shields.io/pypi/v/megabrain?style=flat-square&color=3776AB" alt="PyPI"></a>
  <a href="https://github.com/bernatch22/megabrain/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/bernatch22/megabrain/ci.yml?style=flat-square&label=CI" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT">
  <img src="https://img.shields.io/badge/retrieval-no%20LLM%20·%20~200ms-2ea44f?style=flat-square" alt="No LLM in the retrieval path">
  <img src="https://img.shields.io/badge/code-zero%20hallucination-6f42c1?style=flat-square" alt="Zero code hallucination">
  <img src="https://img.shields.io/badge/MCP-ready-000000?style=flat-square" alt="MCP ready">
</p>

---

**megabrain** is a local code-intelligence engine. It replaces minutes of file-by-file
crawling — grep, read, explore-agent chains — with a single grounded answer. Index a repo
once; every later question retrieves *all* the related code and stitches it into a
walkthrough narrated by an LLM that can **only point at code, never rewrite it** — so
nothing is hallucinated. Retrieval itself uses **no LLM** (~200 ms); the one LLM call
just narrates.

## Languages

Code is chunked over its real AST (the [cAST](https://arxiv.org/abs/2506.15655)
split-then-merge recipe), so chunks are whole functions/classes with breadcrumbs — never
arbitrary line windows.

| | languages | how |
|---|---|---|
| **built-in** | **Python**, **TypeScript / JS / JSX / TSX / MJS / CJS**, **Markdown** | stdlib `ast` · tree-sitter · no-LLM doc chunker |
| **`[languages]` extra** | **Ruby**, **Go**, **Rust**, **PHP** | tree-sitter grammars |

Adding a language is a `LangSpec` entry + `pip install tree_sitter_<lang>` — a config
entry in a registry, not a branch in the indexer ([CONTRIBUTING](CONTRIBUTING.md) has the
recipe). Import/call **graph** edges are built for Python, TS/JS and PHP (`use`-statement
resolution) today; other languages retrieve on dense+lexical signals (no graph needed for
correctness). Legacy procedural PHP (2000s-era mixed HTML/SQL pages) gets its own
section-aware chunker; modern PSR/namespaced files keep the generic one.

## Install

```bash
pip install megabrain                 # core: Python · TS/JS · Markdown
pip install 'megabrain[languages]'    # + Ruby · Go · Rust · PHP
```

From a clone, for development:

```bash
git clone https://github.com/bernatch22/megabrain.git && cd megabrain
pip install -e '.[languages]'
python3 -m pytest                      # offline test suite — no network, no key
```

## Setup

One key, read from the environment (with a `~/.zshrc` fallback):

```bash
export OPENROUTER_API_KEY=...          # embeddings + ask, all via OpenRouter
```

Everything runs through OpenRouter's OpenAI-compatible API, so **any model works** — pick
per role by env (the defaults reproduce the validated stack):

```bash
export MEGABRAIN_EMBED_MODEL=perplexity/pplx-embed-v1-0.6b   # embeddings (default)
export MEGABRAIN_ASK_MODEL=qwen/qwen3-coder                  # ask narrator (default; ~5x cheaper than Haiku, on par)
```

## Usage

```bash
megabrain index  ~/repo                                    # incremental (sha256), no daemon
megabrain ask    ~/repo "how does auth work end to end"    # walkthrough + real code (~6–20s)
megabrain ask    ~/repo/src/auth "how are tokens issued"   # scope to a sub-path (path-scope)
megabrain ask    ~/repo "how do I configure X" --docs      # explain the docs, not the code
megabrain ask    ~/repo "..." --with-docs                  # code AND docs together
megabrain query  ~/repo "request retry logic"              # raw code map, no LLM (~200ms)
megabrain query  ~/repo "..." --full                       # + RELATED code bodies (heavier; default is map-only)
megabrain query  ~/repo "..." --best                       # + LLM order-rerank (~2s, never drops files)
megabrain get    ~/repo src/x.py --symbol Class.method     # one file or symbol
megabrain chunks ~/repo src/x.py "query"                   # every chunk of one file, scored (JSON)
megabrain stats  ~/repo                                    # index stats
megabrain serve-api ~/repo --port 2134                     # long-running JSON API (warm state)
```

**Path-scope:** pass a sub-folder (`~/repo/src/auth`) to any of `ask` / `query` / `get`
and retrieval is confined to files under it — the repo root (where the index lives) is
auto-detected. Multi-repo works too: `megabrain query ~/a/src,~/b "..."`.

**Always fresh:** `ask` / `query` / `chunks` auto-refresh a stale index (60 s TTL,
incremental — only changed files re-embed) before answering, so results always match
what's on disk. Same behavior as the MCP server.

**Excluding files:** build artifacts (`node_modules`, `.venv`, `dist`, …) are skipped by
default. Add your own with `megabrain index ~/repo --exclude generated --exclude '*.pb.go'`
or a persistent **`.megabrainignore`** at the repo root (one pattern per line; a bare name
matches any path segment, a glob or `path/` matches the repo-relative path):

```
generated/
vendor
*.min.js
docs/legacy
```

## See it live — web demo

```bash
git clone https://github.com/bernatch22/megabrain && cd megabrain
pip install -e .                       # + OPENROUTER_API_KEY (or a local embed endpoint)
python examples/webui/server.py        # → http://localhost:8688
```

It ships with a 2003-style **legacy-PHP sample app** (indexed on the first run,
~30 s). Type a question → the real engine ranks the bundle files — **CORE** /
**RELATED**, in milliseconds, no LLM → click a file → every chunk of it renders
**scored**, with the chunks retrieval actually *selected* highlighted and the noise
dimmed. Nothing is precomputed: every query runs the same `search` + `chunks_for_file`
path agents use. Point it at your own code too:

```bash
python examples/webui/server.py ~/my/repo /tmp/click   # any repos, auto-indexed
```

## Provider flexibility — cloud, native, local, hybrid

Embeddings and chat can **each** point at any OpenAI-compatible endpoint. localhost
servers (Ollama / LM Studio / vLLM) need **no API key**:

```bash
# native provider (e.g. A/B a model directly):
export MEGABRAIN_EMBED_BASE_URL=https://api.perplexity.ai/v1   # uses PERPLEXITY_API_KEY

# hybrid — local private embeddings + cheap OpenRouter narration:
export MEGABRAIN_EMBED_BASE_URL=http://localhost:11434/v1
export MEGABRAIN_EMBED_MODEL=embeddinggemma
export MEGABRAIN_EMBED_BATCH=8            # smaller requests for local servers

# fully local (decent GPU) — nothing leaves the machine:
export MEGABRAIN_CHAT_BASE_URL=http://localhost:11434/v1
export MEGABRAIN_ASK_MODEL=qwen3-coder:30b
```

Changing the embed model auto-triggers a full re-embed on the next `index` (or force it
with `--force`), so vectors never silently mismatch. Local-stack benchmarks live in
`evals/LOCAL_MODELS.md`.

### `ask` on Claude — Claude Code credits (default when installed), or the Anthropic API

The narrator (`ask` / `--best`) runs on **Claude** by default when the Claude Agent SDK is
installed, with the same live streaming — so a Claude Code user gets subscription-credit
narration with zero config:

```bash
pip install 'megabrain[claude]'        # Claude Agent SDK → ask now runs on Claude (haiku)
```

**Credentials** — the SDK drives the Claude Code CLI, so it uses whatever Claude Code
already has:

- **Claude Code subscription (recommended)** — if the `claude` CLI is installed and
  logged in, `ask` runs on your plan's credits. No API key, nothing else to configure.
- **Anthropic API** — set `ANTHROPIC_API_KEY` and the same setup bills your API account.

**Choosing the provider** — the default is auto (Claude when its SDK is importable, else
OpenRouter, so a plain `pip install megabrain` always works). Pin it either way, per run:

```bash
export MEGABRAIN_CHAT_PROVIDER=claude       # force Claude   (or 'openrouter' to force that)
export MEGABRAIN_ASK_MODEL=sonnet           # optional — any Claude model or alias

MEGABRAIN_CHAT_PROVIDER=openrouter megabrain ask ~/repo "..."   # one-off on OpenRouter
```

> **Embeddings are a separate lane and always need OpenRouter (or a local embed endpoint).**
> `index` / `query` / the web demo embed text, and Anthropic has no embeddings API — so
> `OPENROUTER_API_KEY` (or `MEGABRAIN_EMBED_BASE_URL` → a local server like Ollama) is
> required regardless of the chat provider. The Claude switch only covers the chat side
> (`ask` and `--best`).

## How it works

A three-stage pipeline. **Only `ask` calls an LLM — and only to narrate.**

| stage | what it does |
|---|---|
| **index** | cAST chunk → embed (`pplx-embed-v1-0.6b`, int8, L2-normalized) → SQLite. Incremental by `sha256`, no watcher. |
| **query** | No-LLM retrieval (~200 ms): dense-chunk + file-skeleton fusion, with import/call-graph candidates. Returns a map — **CORE** (full code of the top files) + **RELATED** (every connected file: best-match span + symbols; `--full` adds their code bodies). The default map is ~60% fewer tokens than inlining RELATED code — sized for agent context windows — while the bundle keeps every file (golden bundle_full stays 1.00). |
| **ask** | One streamed chat call (qwen3-coder via OpenRouter by default; Claude via `MEGABRAIN_CHAT_PROVIDER=claude`) writes the walkthrough and cites code as `[[k]]`; the engine **replaces each citation with the verbatim block** (real file, real line numbers). Non-cited files are listed at the end. Fail-open: any API error falls back to the full `query` bundle. |

Because the model only emits citations and the engine splices code from disk, **code
cannot be hallucinated or rewritten.**

## MCP

Use it from Claude Code or any MCP client:

```bash
claude mcp add megabrain -- python3 -m megabrain.mcp_server
```

Tools: `megabrain_ask` (primary), `megabrain_query`, `megabrain_get`, `megabrain_chunks`,
`megabrain_index` — `ask`/`query` take an optional `scope_path` for sub-path retrieval.
The server auto-refreshes a stale index before answering, so results always match disk.

## HTTP API

`serve-api` keeps the index warm in memory and serves retrieval over HTTP (stdlib only —
no framework). Embed it in an app, or front a docs site with real semantic search.

```bash
megabrain serve-api ~/repo --port 2134 [--host 0.0.0.0] [--cors https://site] [--no-llm] [--token SECRET]
```

| route | returns |
|---|---|
| `POST /search` `{query}` | raw bundle (`tier1` / `tier2`), same as `query` |
| `GET /docsearch?q=` | doc-search hits — `{title, slug, snippet, context, score, group}` |
| `GET /chunks?file=&q=` | every chunk of one file: span, score, selected flag |
| `POST /ask` `{question}` | LLM walkthrough (`{text, …}`) |
| `GET /get?file=&symbol=` · `POST /index` · `GET /health` | one file/symbol · reindex · status |

Binds localhost by default; `--cors` opts into a browser origin. Off-localhost, set
`--token` (or `MEGABRAIN_API_TOKEN`) — it requires `Authorization: Bearer <token>` on every
route except `/health`. `/docsearch` groups are configurable per deployment via
`.megabrain/docsearch.json` (`{"api/": "SDK API", …}`) or `MEGABRAIN_DOCSEARCH_GROUPS`.

## Design

Every choice below is backed by an internal golden set (30 verified queries):

| decision | evidence |
|---|---|
| cAST chunking (4K nws chars, breadcrumbs, partition-guaranteed) | unit-tested; every line lands in exactly one chunk — no gaps, no overlaps |
| `pplx-embed-v1` via OpenRouter (1024-d, int8 wire, **L2-normalized**) | beat `openai-3-large` on code in a bakeoff; ~$0.0016/repo |
| dense chunk + 0.5 × file-skeleton score | dual-granularity; precision up, no downside |
| graph (import + call edges) for candidates only | PageRank-as-ranking **rejected** by data (Acc@1 0.91 → 0.73) |
| **no LLM in the retrieval path** | every LLM *prune* variant cost completeness; `ask` explains, it never prunes |

**Engine retrieval** (internal golden set): R@1 **0.86** · bundle\_full **1.00** · p50 **~10 ms** warm.
**SWE-bench Lite** localization (no training): retrieval Acc@1 ≈ 0.52 / @5 ≈ 0.83 — on par
with the trained CodeRankEmbed retriever.

## Python API

```python
import megabrain

megabrain.index_repo("path/to/repo")
res = megabrain.search("path/to/repo", "how are tokens issued")  # {tier1, tier2, ...}
print(megabrain.render(res))

from megabrain.ask import ask, render_ask                        # LLM walkthrough
print(render_ask(ask("path/to/repo", "how does auth work end to end")))
```

`import megabrain` is dependency-lazy (numpy/tree-sitter load on first use), and the
package ships `py.typed`. Long-running apps: `load_state()` once +
`search_with_state()` per query (that's exactly what `serve-api` does).

**Custom chunkers** — teach the engine a new content type without forking. Any object
with `exts` + `chunk_file(relpath, source) -> FileResult` (chunks must be an exact line
partition — check with `validate_partition`) plugs in via:

```python
megabrain.index_repo("path/to/repo", strategies=[MySqlStrategy()])
```

Custom strategies are matched **before** the built-ins, so they can claim a new
extension (`.sql`, `.proto`, …) or override how an existing one is chunked; everything
downstream (embedding, retrieval, `ask`) is content-agnostic. Runnable examples —
programmatic API, a complete `.sql` chunker, a terminal chunk heatmap, and a **live
web demo** (`python examples/webui/server.py` → ask a question, watch retrieval rank
the files and light up the selected chunks on a bundled legacy-PHP sample or any repo
you pass) — in [`examples/`](examples/).

## Project layout

```
megabrain/            engine — providers, embeddings, SQLite store, graph, indexer, query, ask, serve, cli, mcp_server
megabrain/chunkers/   cAST chunkers behind one FileResult contract (python · treesitter+LangSpec · php · markdown)
tests/                offline suite (no network/key/corpus) — run with `python3 -m pytest`
evals/                golden set + model bakeoffs (maintainer-side, private corpus)
```

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) (the best first PR is a new
language `LangSpec`). Security reports: [SECURITY.md](SECURITY.md).

---

<p align="center"><sub>MIT · github.com/bernatch22/megabrain</sub></p>
