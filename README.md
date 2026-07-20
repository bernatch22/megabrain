<p align="center">
  <img src="https://raw.githubusercontent.com/bernatch22/megabrain/master/assets/megabrain.png" alt="megabrain" width="180">
</p>

<h1 align="center">megabrain</h1>

<p align="center">
  <b>Ask a codebase a question. Get the exact code back.</b>
</p>

<p align="center">
  <sub>The repo walk your coding agent does in <b>10–30 grep-and-open turns</b> — in <b>one call</b>.</sub>
</p>

<p align="center">
  <a href="https://pypi.org/project/megabrain/"><img src="https://img.shields.io/pypi/v/megabrain?style=flat-square&color=3776AB" alt="PyPI"></a>
  <a href="https://github.com/bernatch22/megabrain/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/bernatch22/megabrain/ci.yml?style=flat-square&label=CI" alt="CI"></a>
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT">
  <img src="https://img.shields.io/badge/retrieval-no%20LLM%20·%20~200ms-2ea44f?style=flat-square" alt="No LLM in the retrieval path">
  <img src="https://img.shields.io/badge/MCP-ready-000000?style=flat-square" alt="MCP ready">
</p>

---

Point megabrain at a repo and ask **"how does auth work"** in plain English. It finds all
the related code in ~200 ms with **no LLM** — just math on embeddings, in **one SQLite
file**. No vector DB, no containers, no services.

Want it *explained*? `ask` adds one LLM call that narrates a walkthrough with the **real
code spliced in from disk**, line for line. The model only ever *points* at code — it
cannot rewrite a line, so nothing is invented.

<p align="center">
  <img src="https://raw.githubusercontent.com/bernatch22/megabrain/master/assets/ask-agents.svg" alt="Three acts. One, megabrain_search: no-LLM retrieval ranks the chunks, then the LLM rerank strikes the vocabulary-only matches and reorders what survives. Two, megabrain_ask: a broad question fans out into three parallel sub-agents, one synthesis merges their cited answers with the verbatim code spliced in, and the workflow lands in the flow cache. Three, megabrain_graph: a path query between two files reports that they never call each other and names the file that bridges them." width="900">
</p>

## Quickstart

No API keys — narrate on your Claude Code plan, embed locally:

```bash
pip install 'megabrain[claude]'

ollama pull unclemusclez/jina-embeddings-v2-base-code   # local embeddings, one time
export MEGABRAIN_EMBED_BASE_URL=http://localhost:11434/v1
export MEGABRAIN_EMBED_MODEL=unclemusclez/jina-embeddings-v2-base-code

megabrain index ~/repo                                   # once — incremental after
megabrain ask   ~/repo "how does auth work end to end"
```

Prefer one cloud key for everything? `export OPENROUTER_API_KEY=…` and skip the rest.
All three setups — cloud, hybrid, fully local — in the
**[Guide](docs/GUIDE.md#1-install-and-your-first-answer)**.

## What you get

|  |  |
|---|---|
| 🔎 **Retrieval that can't hallucinate** | No LLM in the search path: dense chunk vectors fused with a file-skeleton signal and the import/call graph. An optional [LLM rerank](docs/GUIDE.md#2-the-two-ways-to-ask) rides on top — fail-open, never inside the core. |
| 💬 **`ask` — the repo, explained** | One call returns a senior-engineer walkthrough of the whole cross-file flow, code spliced verbatim. Broad questions [fan out into parallel sub-agents](docs/GUIDE.md#2-the-two-ways-to-ask). |
| 🖥️ **A local studio** | `megabrain studio` — search, ask, the flow cache and a live knowledge graph in your browser, plus a read-only code navigator. [Tour →](docs/GUIDE.md#3-the-studio) |
| 🕸️ **A knowledge graph, free** | The same index doubles as a map: communities, core files, and the real call-path between any two. numpy only, no networkx. [What it's for →](docs/GUIDE.md#4-map-the-repo-with-the-graph) |
| ⚡ **It learns from itself** | Every `ask` caches its walkthrough. Ask again — even reworded — and it serves in **~0 ms with zero LLM** (measured 27.8 s → 0.19 s), sha-guarded so it can never describe changed code. [How →](docs/GUIDE.md#5-it-remembers--the-flow-cache) |
| 🔌 **Everywhere you work** | A CLI, an **MCP server** for Claude Code / Codex / Cursor / Gemini CLI, a Python library, and the studio. |

**[→ Try it live](https://bernardocastro.dev/megabrain/demo/)** — seven popular
open-source repos, the real engine, read-only.

## For coding agents — one call instead of thirty turns

This is what megabrain is *for*. Dropped into an unfamiliar repo, an agent burns 10–30
tool turns — grep, open a file, follow an import, grep again — before it writes a line,
and the picture it assembles is still its own guess.

```bash
megabrain install    # detects Claude Code · Codex · Cursor · Windsurf · Gemini CLI · Antigravity
```

|  | by hand (grep + read chains) | one megabrain call |
|---|---|---|
| tool turns | 10–30 | **1** |
| what lands in context | whole files, mostly irrelevant | **exactly the signal chunks** |
| the cross-file story | reconstructed, unverified | **narrated with the real code spliced in** |
| asking it again later | the full re-exploration | **~0 ms, from the flow cache** |

Six tools, deliberately lean — your agent already has Read and Grep for single files:
**`megabrain_ask`** (the default) · `megabrain_search` · `megabrain_graph` ·
`megabrain_index` · `megabrain_forge` · `megabrain_flows`.
**[Parameters →](docs/REFERENCE.md#mcp-tools)** · **[Wiring recipes →](docs/RECIPES.md#give-your-coding-agent-the-whole-repo)**

> Put this in your agent's rules: **for any question about how the code works, call
> `megabrain_ask` FIRST, before grepping.** One call returns the whole flow with the real
> code — that single instruction is the difference between 15 turns and 1.

## Commands

```bash
megabrain index  ~/repo                       # build / update the index (incremental)
megabrain ask    ~/repo "how does X work"     # narrated walkthrough + real code
megabrain search ~/repo "retry logic"         # the code map, no LLM (~200 ms)
megabrain graph  ~/repo                       # the repo as a knowledge graph
megabrain studio                              # the web UI + JSON API
megabrain install                             # register the MCP server
```

**[Every command and flag →](docs/REFERENCE.md#cli)**

## Measured, not vibes

Against [claude-context](https://github.com/zilliztech/claude-context) (Zilliz), the
closest open-source peer — same repo, same 22 hand-labelled questions, both at their best:

|  | megabrain | claude-context |
|---|---|---|
| **R@1** | **0.864** | 0.818 |
| **R@5** | **1.000** | 0.909 |
| search latency | **~22 ms** warm | ~1400 ms |
| vector store | **one SQLite file** | Milvus + etcd + MinIO |
| narrated answer | **yes** — real code spliced in | no (returns chunks) |

The golden set is ours, on a corpus megabrain was tuned against — treat the absolute
numbers as home-field and run it yourself. **[Full method, caveats and the embedding
bakeoff →](ARCHITECTURE.md#8-evidence-where-the-numbers-live)**

## Docs

|  |  |
|---|---|
| **[Guide](docs/GUIDE.md)** | The tour, front to back: setup → search vs ask → the studio → the graph → the flow cache → MCP → new file types → tuning |
| **[Recipes](docs/RECIPES.md)** | "I want to ___" — private repos, team knowledge bases, public demos, custom file types, cost and speed |
| **[Reference](docs/REFERENCE.md)** | Every CLI flag, MCP tool, HTTP route, env var and config file |
| **[Architecture](ARCHITECTURE.md)** | How it's built and **why** — the locked design rules and the experiments behind them |
| **[Contributing](CONTRIBUTING.md)** | The best first PR is a new language |
| **[Changelog](CHANGELOG.md)** | What changed, and why |

Languages: **Python · JS/TS · Markdown** built in · **Ruby · Go · Rust · PHP** with
`pip install 'megabrain[languages]'` · anything else via
[`megabrain forge`](docs/GUIDE.md#7-teach-it-your-file-types).

---

<p align="center"><sub>MIT · github.com/bernatch22/megabrain</sub></p>
