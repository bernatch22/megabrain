<p align="center">
  <img src="https://raw.githubusercontent.com/pinecall/megabrain/master/assets/megabrain.png" alt="megabrain" width="180">
</p>

<h1 align="center">megabrain</h1>

<p align="center">
  <b>One call returns all the code related to a question</b><br>
  — explained like a senior engineer, with the real code spliced in.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/retrieval-no%20LLM%20·%20~200ms-2ea44f?style=flat-square" alt="No LLM in the retrieval path">
  <img src="https://img.shields.io/badge/code-zero%20hallucination-6f42c1?style=flat-square" alt="Zero code hallucination">
  <img src="https://img.shields.io/badge/MCP-ready-000000?style=flat-square" alt="MCP ready">
</p>

---

**megabrain** is a local code-intelligence engine. It replaces minutes of file-by-file
crawling — grep, read, explore-agent chains — with a single grounded answer. Index a repo
once; every later question retrieves *all* the related code and stitches it into a
walkthrough narrated by an LLM that can **only point at code, never rewrite it** — so
nothing is hallucinated.

## Install

```bash
pip install megabrain                 # core: Python · TS/JS · markdown
pip install 'megabrain[languages]'    # + Ruby · Go · Rust
```

Or from a clone, for development:

```bash
git clone https://github.com/pinecall/megabrain.git && cd megabrain
pip install -e .
```

One key, read from the environment (with a `~/.zshrc` fallback):

```bash
export OPENROUTER_API_KEY=...   # required — embeddings + ask/--best, all via OpenRouter
```

Everything runs through OpenRouter's OpenAI-compatible API, so any model works — pick
per role via env (defaults reproduce the validated stack exactly):

```bash
export MEGABRAIN_EMBED_MODEL=perplexity/pplx-embed-v1-0.6b   # embeddings (default)
export MEGABRAIN_ASK_MODEL=qwen/qwen3-coder                  # ask / --best (default; ~5x cheaper than haiku, on par)
```

Embeddings and chat can each point at ANY OpenAI-compatible endpoint instead of
OpenRouter — a provider's native API, or a **local server** (Ollama / LM Studio / vLLM;
localhost needs no API key):

```bash
# native provider (A/B testing):
export MEGABRAIN_EMBED_BASE_URL=https://api.perplexity.ai/v1
export MEGABRAIN_EMBED_MODEL=pplx-embed-v1-0.6b   # uses PERPLEXITY_API_KEY

# hybrid: local embeddings (Ollama) + OpenRouter chat — private index, cheap ask:
export MEGABRAIN_EMBED_BASE_URL=http://localhost:11434/v1
export MEGABRAIN_EMBED_MODEL=embeddinggemma       # 300M, runs on any laptop

# fully local (decent GPU, ~24GB — see evals/LOCAL_MODELS.md):
export MEGABRAIN_CHAT_BASE_URL=http://localhost:11434/v1
export MEGABRAIN_ASK_MODEL=qwen3-coder:30b        # 30B MoE via Ollama
```

Re-index after any embed-model change — megabrain detects it and re-embeds automatically
(or force it: `megabrain index <repo> --force`).

## Usage

```bash
megabrain index  ~/repo                                      # incremental (sha256), no daemon
megabrain ask    ~/repo "how does auth work end to end"      # walkthrough + real code (~6–20s)
megabrain ask    ~/repo "how do I configure X" --docs        # explain the docs instead of code
megabrain query  ~/repo "request retry logic"                # raw code map, no LLM (~200ms)
megabrain get    ~/repo src/x.py --symbol Class.method       # one file or symbol
megabrain serve-api ~/repo --port 2134                       # long-running JSON API (warm state)
```

Indexes code (`.py` · `.ts` · `.tsx` · `.js` · `.jsx` · `.mjs` · `.cjs` · Ruby · Go · Rust) and
markdown (`.md` · `.markdown` · `.mdx`) through a **strategy registry** — adding a language
or content type is a config entry, not a branch in the indexer.

## How it works

A three-stage pipeline. **Only `ask` calls an LLM — and only to narrate.**

| stage | what it does |
|---|---|
| **index** | cAST chunk → OpenRouter embed (`pplx-embed-v1-0.6b`, int8, L2-normalized) → SQLite. Incremental by `sha256`, no watcher. |
| **query** | No-LLM retrieval (~200ms): dense-chunk + file-skeleton fusion, with import/call-graph candidates. Returns a map — **CORE** (full code of the top files) + **RELATED** (every connected file with its best chunk). |
| **ask** | One streamed OpenRouter chat call (qwen3-coder by default) writes the walkthrough and cites code as `[[k]]`; the engine **replaces each citation with the verbatim block** (real file, real line numbers). Non-cited related files are listed at the end. Fail-open: any API error falls back to the full `query` bundle. |

Because the model only emits citations and the engine splices code from disk, **code cannot
be hallucinated or rewritten.**

## MCP

Use it from Claude Code or any MCP client:

```bash
claude mcp add megabrain -- python3 -m megabrain.mcp_server
```

Tools: `megabrain_ask` (primary), `megabrain_query`, `megabrain_get`, `megabrain_index`.
The server auto-refreshes a stale index before answering, so results always match disk.

## HTTP API

`serve-api` keeps the index warm in memory and serves retrieval over HTTP (stdlib only —
no framework). Embed it in any app, or front a static site with semantic search.

```bash
megabrain serve-api ~/repo --port 2134 [--host 0.0.0.0] [--cors https://site] [--no-llm]
```

| route | returns |
|---|---|
| `POST /search` `{query}` | raw bundle (`tier1` / `tier2`), same as `query` |
| `GET /docsearch?q=` | doc-search hits — `{title, slug, snippet, context, score, group}` |
| `POST /ask` `{question}` | LLM walkthrough (`{text, …}`) |
| `GET /get?file=&symbol=` · `POST /index` · `GET /health` | one file/symbol · reindex · status |

State loads once and reloads only when the index changes on disk, so each query skips the
SQLite matrix load. Binds localhost by default (front it with a reverse proxy); `--cors`
opts into a browser origin.

## Design

Every choice below is backed by an internal golden set (30 verified queries):

| decision | evidence |
|---|---|
| cAST chunking (4K nws chars, breadcrumbs, partition-guaranteed) | unit-tested; every line lands in exactly one chunk — no gaps, no overlaps |
| `pplx-embed-v1` via OpenRouter (1024-d, int8 wire, **L2-normalized**) | beats `openai-3-large` on code; ~$0.0016/repo |
| dense chunk + 0.5 × file-skeleton score | dual-granularity; precision up, no downside |
| graph (import + call edges) for candidates only | PageRank-as-ranking **rejected** by data (Acc@1 0.91 → 0.73) |
| **no LLM in the retrieval path** | every LLM *prune* variant cost completeness; `ask` explains, it never prunes |

**Engine retrieval** (internal golden set): R@1 **0.86** · bundle\_full **1.00** · p50 **8 ms** warm.
**SWE-bench Lite** localization (no training): retrieval Acc@1 ≈ 0.52 / @5 ≈ 0.83 — on par
with the trained CodeRankEmbed retriever.

## Project layout

```
megabrain/   engine — chunkers, embeddings, SQLite store, graph, indexer, query, ask, serve, cli, mcp_server
evals/       golden.json (30 verified queries) + swebench harness
tests/       engine + chunker gates
```

---

<p align="center"><sub>github.com/pinecall/megabrain</sub></p>
