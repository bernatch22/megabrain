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

<br>

Point megabrain at a repo and ask **"how does auth work"** in plain English. It finds all
the related code in ~200 ms with **no LLM** — just math on embeddings, in **one SQLite
file**. No vector DB, no containers, no services.

Want it *explained*? `ask` adds one LLM call that narrates a walkthrough with the **real
code spliced in from disk**, line for line. The model only ever *points* at code — it
cannot rewrite a line, so nothing is invented.

<br>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/bernatch22/megabrain/master/assets/studio-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/bernatch22/megabrain/master/assets/studio-light.png">
    <img alt="megabrain studio's Ask tab on sinatra: one question served instantly from the flow cache, and below it a live synthesis — retrieval in 25 ms across 14 files, then the cited answer streaming with the real code spliced in." src="https://raw.githubusercontent.com/bernatch22/megabrain/master/assets/studio-light.png" width="900">
  </picture>
</p>

<p align="center">
  <sub><code>megabrain studio</code> — the whole engine in your browser</sub>
  <br><br>
  <a href="https://bernardocastro.dev/megabrain/demo/"><b>Try it live →</b></a>
</p>

<br>

---

## Quickstart

### Best quality — one key, nothing to configure

```bash
pip install megabrain
export OPENROUTER_API_KEY=sk-or-...

megabrain index ~/repo                            # once — incremental after
megabrain ask   ~/repo "how does auth work end to end"
```

That single key gets you both halves of the validated stack, and they're already the
defaults:

- **`perplexity/pplx-embed-v1-0.6b`** for retrieval — the measured best for code recall.
  It beat pplx-4b, codestral-embed, openai-3-large and bge-m3 in a head-to-head bakeoff
  (R@1 **0.864**, bundle_full **0.955**).
- **`google/gemini-3.1-flash-lite`** for narration — the fastest and cheapest tier,
  at the quality of models costing several times more. A full walkthrough in seconds, for
  fractions of a cent.

### No keys — your Claude plan + local embeddings

Narration runs on the Claude Code subscription you already pay for, embeddings run on your
machine, and **your code never leaves it**:

```bash
pip install 'megabrain[claude]'                   # narrates on your Claude Code login

unset ANTHROPIC_API_KEY                           # ← or it bills the API, not your plan

ollama pull bge-m3                                # local embeddings, one time
export MEGABRAIN_EMBED_BASE_URL=http://localhost:11434/v1
export MEGABRAIN_EMBED_MODEL=bge-m3

megabrain index ~/repo
megabrain ask   ~/repo "how does auth work end to end"
```

**That `unset` is the line people miss.** megabrain narrates through the Claude Agent SDK,
which drives the Claude Code CLI — and the CLI takes an API key over your login. With
`ANTHROPIC_API_KEY` exported, every `ask` quietly bills the Anthropic API per token while
the subscription you already pay for sits unused. Nothing warns you; the answers are
identical. `unset` covers the current shell only, so if the key comes from your `~/.zshrc`
or `~/.bashrc`, drop it there too — or keep it and pick per-shell which one pays.

`bge-m3` is the local embedder to use. It matches the cloud one on the measure that
decides whether `ask` gets the right code at all, and trails it on ranking the single best
file first — a real trade, and a small one.

### Fully local — Ollama for both halves, zero cloud

Air-gapped, $0, open weights end to end:

```bash
pip install 'megabrain[languages]'
ollama pull bge-m3 && ollama pull qwen3-coder:30b

export MEGABRAIN_EMBED_BASE_URL=http://localhost:11434/v1
export MEGABRAIN_EMBED_MODEL=bge-m3
export MEGABRAIN_CHAT_BASE_URL=http://localhost:11434/v1
export MEGABRAIN_ASK_MODEL=qwen3-coder:30b

export MEGABRAIN_ASK_CTX_CHARS=105000     # ← required: see below
export OLLAMA_CONTEXT_LENGTH=40960

megabrain index ~/repo --force            # --force re-embeds with the new model
megabrain ask   ~/repo "how does auth work end to end"
```

**Use a real coder model.** `qwen3-coder` is the one that holds up — the small dense models
are not a cheaper trade-off, they cite less *and* run slower, and a general-purpose model of
the same size does markedly worse on code.

**`MEGABRAIN_ASK_CTX_CHARS` is not optional.** `ask`'s budget is sized for cloud context
windows, so a local model silently gets a truncated prompt — no error, just quietly worse
answers. Compared to the cloud you lose some *secondary* citations, never correctness: the
code you're shown is still spliced verbatim from disk.

**[The numbers, and the extra knob thinking models need →](docs/RECIPES.md#run-fully-local--no-keys-no-cloud)**

---

Other languages need one extra install: `pip install 'megabrain[languages]'` adds
Ruby · Go · Rust · PHP. Python, JS/TS and Markdown work out of the box.
Every setup, with its cost: **[Guide](docs/GUIDE.md#1-install-and-your-first-answer)**.

---

## What you get

**Retrieval that cannot hallucinate.** The search path has no LLM at all — dense chunk
vectors fused with a file-skeleton signal and the import/call graph. The narrator only
ever *cites* spans and the engine splices the verbatim bytes, so no line is ever invented.
An optional [LLM rerank](docs/GUIDE.md#2-the-two-ways-to-ask) rides on top to drop
vocabulary-only matches — fail-open, never inside the core path.

**`ask` — the repo, explained.** One call returns a senior-engineer walkthrough of the
whole cross-file flow, with the real code spliced in at each step. Broad questions
[fan out into parallel sub-agents](docs/GUIDE.md#2-the-two-ways-to-ask), one per subsystem,
and a synthesizer merges their cited answers.

**It learns from itself.** Every `ask` caches its walkthrough. Ask again — even reworded —
and it serves in **~0 ms with zero LLM** (measured 27.8 s → 0.19 s), guarded by a
byte-level sha recheck so it can never describe code that changed.
[How it works →](docs/GUIDE.md#5-it-remembers--the-flow-cache)

**A knowledge graph, for free.** The same index doubles as a navigable map: communities,
the core "god node" files, and the real call-path between any two files — built from AST
edges plus embedding similarity, numpy only, no networkx.
[What it's actually good for →](docs/GUIDE.md#4-map-the-repo-with-the-graph)

**A local studio.** `megabrain studio` opens the whole engine in your browser: search,
ask, the flow cache and the graph on a live canvas, plus a read-only code navigator where
every identifier is a go-to-definition link. [Take the tour →](docs/GUIDE.md#3-the-studio)

**Everywhere you work.** A terminal CLI, an MCP server inside Claude Code / Codex / Cursor
/ Gemini CLI, a Python library, and the studio.

---

## For coding agents

This is what megabrain is *for*. Dropped into an unfamiliar repo, an agent burns 10–30
tool turns — grep, open a file, follow an import, grep again — before it writes a line,
and the picture it assembles is still its own guess.

```bash
megabrain install    # detects Claude Code · Codex · Cursor · Windsurf · Gemini CLI · Antigravity
```

|  | by hand | one megabrain call |
|---|---|---|
| tool turns | 10–30 | **1** |
| what lands in context | whole files, mostly irrelevant | **exactly the signal chunks** |
| the cross-file story | reconstructed, unverified | **narrated, real code spliced in** |
| asking it again later | the full re-exploration | **~0 ms, from the cache** |

Your agent gets six tools, deliberately lean — it already has Read and Grep for single
files: **`megabrain_ask`** (the default) · `megabrain_search` · `megabrain_graph` ·
`megabrain_index` · `megabrain_forge` · `megabrain_flows`.

> **Put this in your agent's rules:** for any question about how the code works, call
> `megabrain_ask` **first**, before grepping. One call returns the whole flow with the
> real code — that single instruction is the difference between 15 turns and 1.

[Every parameter →](docs/REFERENCE.md#mcp-tools) ·
[Wiring recipes →](docs/RECIPES.md#give-your-coding-agent-the-whole-repo)

---

## Commands

```bash
megabrain index  ~/repo                       # build / update the index (incremental)
megabrain ask    ~/repo "how does X work"     # narrated walkthrough + real code
megabrain search ~/repo "retry logic"         # the code map, no LLM (~200 ms)
megabrain graph  ~/repo                       # the repo as a knowledge graph
megabrain studio                              # the web UI + JSON API
megabrain install                             # register the MCP server
```

[Every command and flag →](docs/REFERENCE.md#cli)

---

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
numbers as home-field and run it yourself.
[Full method, caveats and the embedding bakeoff →](ARCHITECTURE.md#8-evidence-where-the-numbers-live)

---

## Docs

- **[Guide](docs/GUIDE.md)** — the tour, front to back: setup → search vs ask → the studio
  → the graph → the flow cache → MCP → new file types → tuning
- **[Recipes](docs/RECIPES.md)** — "I want to ___": private repos, team knowledge bases,
  public demos, custom file types, cost and speed
- **[Reference](docs/REFERENCE.md)** — every CLI flag, MCP tool, HTTP route and env var
- **[Architecture](ARCHITECTURE.md)** — how it's built and **why**: the locked design
  rules and the experiments behind them
- **[Contributing](CONTRIBUTING.md)** — the best first PR is a new language
- **[Changelog](CHANGELOG.md)** — what changed, and why

<br>

---

<p align="center"><sub>MIT · github.com/bernatch22/megabrain</sub></p>
