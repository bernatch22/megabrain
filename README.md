<p align="center">
  <img src="https://raw.githubusercontent.com/bernatch22/megabrain/master/assets/megabrain.png" alt="megabrain" width="180">
</p>

<h1 align="center">megabrain</h1>

<p align="center">
  <b>Ask a codebase a question. Get the exact code back.</b>
</p>

<p align="center">
  <a href="https://pypi.org/project/megabrain/"><img src="https://img.shields.io/pypi/v/megabrain?style=flat-square&color=3776AB" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT">
  <img src="https://img.shields.io/badge/retrieval-no%20LLM%20·%20~200ms-2ea44f?style=flat-square" alt="No LLM in the retrieval path">
  <img src="https://img.shields.io/badge/MCP-ready-000000?style=flat-square" alt="MCP ready">
</p>

---

Point megabrain at a repo and ask **"how does auth work"** in plain English. It finds
*all* the related code — in ~200 ms, using **no LLM**, just math on embeddings — and an
LLM narrates a walkthrough with the **real code spliced in from disk**. Nothing is
invented: every line shown is copied verbatim.

Use it from the terminal, as an **MCP server inside Claude Code**, or as a Python library.

## Quickstart — the easy path, no API keys

Everything runs on your machine: `ask` narrates on your **Claude Code** subscription,
embeddings run locally on **Ollama**. No cloud keys.

```bash
pip install 'megabrain[claude]'                      # engine + Claude Code narration

ollama pull nomic-embed-text                          # local embeddings, one time
export MEGABRAIN_EMBED_BASE_URL=http://localhost:11434/v1
export MEGABRAIN_EMBED_MODEL=nomic-embed-text

megabrain index ~/your/repo                           # once — incremental after
megabrain ask   ~/your/repo "how does auth work end to end"
```

`ask` uses your logged-in `claude` CLI (free on your plan); embeddings never leave your
machine. No OpenRouter, no Anthropic key.

## Inside Claude Code

Register it as an MCP server and research any indexed repo without leaving Claude Code:

```bash
claude mcp add megabrain -- python3 -m megabrain.mcp_server
```

Then use `megabrain_ask` / `megabrain_query` instead of grep + Read chains — one call
replaces minutes of file-crawling. Tools: **`megabrain_ask`** (narrated walkthrough),
**`megabrain_query`** (raw code map, no LLM), `megabrain_get`, `megabrain_chunks`,
`megabrain_index`.

## Commands

```bash
megabrain index  ~/repo                          # build / update the index
megabrain ask    ~/repo "how does X work"        # narrated walkthrough + real code
megabrain query  ~/repo "retry logic"            # raw code map, no LLM (~200 ms)
megabrain get    ~/repo src/x.py --symbol Foo    # one file or symbol
megabrain forge  ~/repo                          # teach it your repo's file types (below)
megabrain serve-api ~/repo                       # long-running HTTP API (warm state)
```

Scope to a sub-folder (`~/repo/src/auth`), search several repos at once
(`~/a,~/b`), and the index auto-refreshes when files change on disk.

## Rather use the cloud?

No Claude Code or Ollama? One key runs **everything** through OpenRouter — embeddings
and narration — with sensible defaults:

```bash
export OPENROUTER_API_KEY=...
megabrain ask ~/repo "how does X work"
```

megabrain auto-picks the narrator: **Claude** when its SDK is installed, otherwise
OpenRouter. Embeddings always go through OpenRouter or a local endpoint (Anthropic has no
embeddings API). The full provider matrix — native APIs, hybrid, fully-local GPU — is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## How it works

| stage | what happens |
|---|---|
| **index** | code is split over its syntax tree (whole functions / classes, never arbitrary line windows), embedded once, stored in SQLite. Incremental by hash. |
| **query** | **no LLM** — your question is embedded and matched by vector similarity. Returns every related file in ~200 ms; nothing is dropped. |
| **ask** | one LLM call narrates the answer and cites code as `[[k]]`; the engine replaces each citation with the verbatim block from disk. The model can only *point* at code, never rewrite it — so nothing is hallucinated. Broad questions fan out into parallel sub-agents, then a parent synthesizes. |

Languages: **Python · JS/TS · Markdown** built in; **Ruby · Go · Rust · PHP** with
`pip install 'megabrain[languages]'`.

## forge — megabrain writes its own chunkers

Repos carry more than code: `.toml`, `.yaml`, `.astro`, `.proto`, private DSLs…
Anything outside the registry is invisible to retrieval. `megabrain forge` fixes
that per repo:

```bash
megabrain forge ~/repo --list        # census: which text file types aren't indexed (free)
megabrain forge ~/repo               # LLM-write a chunking strategy per type, validate, install
megabrain forge ~/repo --dry-run     # show the generated code without installing
```

For each uncovered extension, an LLM (same provider stack as `ask`) writes a
`ChunkStrategy` from the contract source + real sample files, and it is only
accepted after chunking **every** matching file in the repo with a clean
exact-line partition (`validate_partition` — failures feed a repair loop, and
nothing unvetted ever installs). The vetted module lands in
`.megabrain/strategies/<ext>.py`, sha-recorded in a user-level trust store
(`~/.megabrain/trust.json`), and from then on every index — including the 60 s
auto-refresh — loads it automatically. Hand-written strategies work the same
way: drop the file in `.megabrain/strategies/` and approve it with
`megabrain trust ~/repo`.

Real run on [pallets/click](https://github.com/pallets/click): forge detected
`.toml` (11 files) and `.yaml` (8 workflows), generated both strategies on the
first attempt (~28 s total), and *"which workflow runs the test suite?"* went
from missing entirely to ranking `.github/workflows/tests.yaml` #1.

## See it live

**[bernardocastro.dev/megabrain](https://bernardocastro.dev/megabrain)** — search 7
popular open-source repos and watch the engine rank the files and pick the exact code
chunks, live. Or run it locally: `python examples/webui/server.py`.

## Learn more

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — the full design, the locked rules, and the measurements behind them
- **[examples/](examples/)** — programmatic API · a custom `.sql` chunker · the web demo
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — the best first PR is a new language

---

<p align="center"><sub>MIT · github.com/bernatch22/megabrain</sub></p>
