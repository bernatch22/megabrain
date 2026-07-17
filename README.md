<p align="center">
  <img src="https://raw.githubusercontent.com/bernatch22/megabrain/master/assets/megabrain.png" alt="megabrain" width="180">
</p>

<h1 align="center">megabrain</h1>

<p align="center">
  <b>Ask a codebase a question. Get the exact code back.</b>
</p>

<p align="center">
  <a href="https://pypi.org/project/megabrain/"><img src="https://img.shields.io/pypi/v/megabrain?style=flat-square&color=3776AB" alt="PyPI"></a>
  <a href="https://github.com/bernatch22/megabrain/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/bernatch22/megabrain/ci.yml?style=flat-square&label=CI" alt="CI"></a>
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT">
  <img src="https://img.shields.io/badge/retrieval-no%20LLM%20·%20~200ms-2ea44f?style=flat-square" alt="No LLM in the retrieval path">
  <img src="https://img.shields.io/badge/MCP-ready-000000?style=flat-square" alt="MCP ready">
  <img src="https://img.shields.io/badge/studio-web%20UI-ff8c3a?style=flat-square" alt="Studio web UI">
</p>

---

Point megabrain at a repo and ask **"how does auth work"** in plain English. It finds
*all* the related code in ~200 ms with **no LLM** — just math on embeddings — then an
LLM narrates a walkthrough with the **real code spliced in from disk**, line for line.

- **Retrieval that cannot hallucinate.** The search path has no LLM at all: dense chunk
  vectors fused with a file-skeleton signal and the import/call graph. The narrator only
  ever *cites* spans — the engine splices the verbatim bytes, so no line is ever invented.
- **A knowledge graph, for free.** The same index doubles as a navigable graph: communities,
  god nodes, and the real call-path between any two files — built from AST edges + embedding
  similarity, numpy-only, no vector DB. `megabrain graph .`
- **Everywhere you work.** A terminal CLI, an **MCP server** inside Claude Code / Codex /
  Cursor / Gemini CLI (+more), a Python library, and a full **local web studio**.

<p align="center">
  <img src="https://raw.githubusercontent.com/bernatch22/megabrain/master/assets/hero.svg" alt="megabrain tracing the call path between two files, hop by hop, with no LLM" width="900">
</p>
<p align="center">
  <em>`megabrain graph . --path` — the real call route between two files, each hop showing the
  function that carries it. No LLM, ~200 ms, built from the index.</em>
</p>

**Get started** (no keys needed — narrate on your Claude Code plan, embed locally):

```bash
pip install 'megabrain[claude]'
megabrain index ~/repo
megabrain ask   ~/repo "how does auth work end to end"
```

## 🖥️ megabrain studio — the whole engine, in your browser

One command turns megabrain into a local studio — nothing canned, every pixel driven by
the live engine:

```bash
megabrain studio ~/repo        #  → open http://localhost:2134
```

- **Search** — every related file ranked in ~200 ms; click one for a **chunk heatmap**
  where signal glows and noise dims, code syntax-highlighted.
- **Prune** — the money shot: what the engine **read** vs what it **ignored**, side by side.
  Flip on **LLM rerank** to watch a cheap model drop the vocabulary-only matches (tests,
  eval scripts) and reorder — fail-open to the deterministic list.
- **Graph** — the repo as a **force-directed knowledge graph**: nodes coloured by community,
  the core abstractions haloed, structural edges solid and semantic ones dotted. Drag, zoom,
  click a file for its neighbours + symbols + real chunks, or type `A -> B` to trace the path
  between two concepts.
- **Ask** — watch a broad question **fan out into parallel sub-agents**, their tool calls
  and prose streaming into per-agent cards, then a synthesis with the **real code spliced
  in** as it types.
- **Providers, live** — Claude SDK · OpenRouter · Ollama, auto-detected. **Switch the
  narrator without leaving the page**, pick the model, and **start `ollama serve` in one
  click** to go fully local.
- **Add a repo → it scans first** — you SEE exactly what will index and what's skipped and
  *why* (`.gitignore` · vendored · generated · too-big), edit the `.megabrainignore`, then a
  **live progress bar** indexes it file by file. The rail also lists **every repo indexed on
  this machine** (the global registry) — studio pre-loads every one of them into the rail,
  so every indexed repo is selectable and searchable immediately.
- **Embeddings you can see** — which model each index used, and **re-index with another**
  (cloud pplx or a local, code-tuned jina) behind the same bar — the query embedding
  switches to match, so search keeps working.

Keyboard-driven, dark/light, zero build step, no CDN. Want the JSON API without the UI?
`megabrain serve-api ~/repo` mounts the exact same endpoints, no studio.

## See it in action

Once the index is built you query it instead of reading files. **Real, verbatim output**
from this very repo:

```text
$ megabrain graph . --path scoring rerank
# graph path — retrieval/scoring.py → retrieval/rerank.py · 285ms
⚠ NOT a call chain — the endpoints never call each other;
  app.py calls BOTH sides (the shared orchestrator)
  retrieval/scoring.py
  └─ call → graph.py        · via _is_test_path, under_path
  └─ call → app.py          · via graph_root, get
  └─ call → retrieval/rerank.py  · via llm_rerank, get
```

That's the whole idea: the graph tells you the **truth** about how two files relate — here,
that they *don't* call each other directly, and it names the file that bridges them
(`app.py`) plus the exact functions on each hop. `--node` opens one file's neighbors + real
code; `--path` traces the route; the studio's **Graph tab** does it on a live canvas with a
`▶ Run the connection` walkthrough. Every carrier is a resolved call site, never a
bare-name guess — `re.search(...)` can't masquerade as your repo's own `search()`.

And retrieval never hallucinates a line:

```text
$ megabrain search . "how does the rerank drop tangential matches" --prune
# megabrain search · 19 signal chunks (31 pruned as noise) · 2ms · no LLM
### 1. [1552] retrieval/rerank.py L1-101 · rerank_model, _hint, llm_rerank · 1.06
### 2. [1772] retrieval/bundle.py  L1-121 · search_with_state              · 0.95
...
```

## Quickstart — the easy path, no API keys

Everything runs on your machine: `ask` narrates on your **Claude Code** subscription,
embeddings run locally on **Ollama**. No cloud keys.

```bash
pip install 'megabrain[claude]'                      # engine + Claude Code narration

ollama pull unclemusclez/jina-embeddings-v2-base-code # local, code-tuned embeddings, one time
export MEGABRAIN_EMBED_BASE_URL=http://localhost:11434/v1
export MEGABRAIN_EMBED_MODEL=unclemusclez/jina-embeddings-v2-base-code

megabrain index ~/your/repo                           # once — incremental after
megabrain ask   ~/your/repo "how does auth work end to end"
```

`ask` uses your logged-in `claude` CLI (free on your plan); embeddings never leave your
machine. No OpenRouter, no Anthropic key.

**Which model?** On Claude Code, `ask` narrates with **Haiku** by default (fast + cheap
on your plan). Bump it with a Claude alias — `export MEGABRAIN_ASK_MODEL=sonnet` (or
`opus`). ⚠️ On the `claude` provider this must be a **Claude** model (`haiku`/`sonnet`/
`opus`/a `claude-*` id), not an OpenRouter slug like `google/…`.

## Inside your AI coding assistant

megabrain speaks **MCP**, and MCP is portable — the same stdio server runs in every
assistant. One command wires up whichever ones you have installed:

```bash
megabrain install            # detects + registers; --list to preview, --remove to undo
```

```text
Registered megabrain in 3 platform(s):
  ✓ Claude Code  registered   ~/.claude.json
  ✓ Codex        registered   ~/.codex/config.toml
  ✓ Antigravity  registered   ~/.gemini/antigravity/mcp_config.json
  · Cursor       skipped (not installed)
```

Supported: **Claude Code · Codex · Antigravity · Cursor · Windsurf · Gemini CLI**
(`--platform <name>` for just one). It only ever writes the `megabrain` key — your other
MCP servers are left alone — and it pins the entry to the interpreter megabrain is
installed in, so re-running it repairs a config that drifted to an old checkout. Prefer
to do it by hand? `claude mcp add megabrain -- python3 -m megabrain.mcp_server`, or copy
the equivalent entry into your assistant's MCP config.

Then use `megabrain_ask` / `megabrain_search` instead of grep + Read chains — one call
replaces minutes of file-crawling. The tools are deliberately lean — megabrain exposes
only what it alone can do (your agent already has Read/Grep for single files):
**`megabrain_ask`** (narrated walkthrough, real code spliced),
**`megabrain_search`** (no core LLM, ~200 ms — a flat, relevance-ranked list of exactly the
chunks worth reading, with the code, noise dropped; an **LLM rerank runs by default**
(`rerank: true`) to cut vocabulary-only matches, fail-open to the raw list),
**`megabrain_graph`** (the repo as a knowledge graph — `mode=map` for communities + core
abstractions + surprising links, `mode=node` for one file's neighbours/symbols/real chunks,
`mode=path` to route between two concepts), `megabrain_index` (index a repo, or `list: true`
to enumerate every repo indexed on this machine), plus `megabrain_forge` (teach it a new file
type) and `megabrain_flows` (the opt-in workflow cache). `megabrain_query` stays as a
deprecated dispatch alias for `megabrain_search`.

## Commands

```bash
megabrain install                                # register the MCP server with your assistants
megabrain index  ~/repo                          # build / update the index
megabrain scan   ~/repo                          # census: what WOULD index + what's skipped & why
megabrain ask    ~/repo "how does X work"        # narrated walkthrough + real code
megabrain search  ~/repo "retry logic"            # raw code map, no LLM (~200 ms)
megabrain search  ~/repo "retry logic" --prune    # flat signal-only chunks, no LLM (drops the noise)
megabrain search  ~/repo "retry logic" --rerank   # + one cheap LLM pass to drop vocabulary-only hits
megabrain graph  ~/repo                          # the repo as a knowledge graph (communities + core nodes)
megabrain graph  ~/repo --node scoring.py        # one file: neighbours, semantic twins, real chunks
megabrain graph  ~/repo --path "auth" "billing"  # BFS route between two concepts (resolved by embedding)
megabrain repos                                  # every repo indexed on this machine (the registry)
megabrain get    ~/repo src/x.py --symbol Foo    # one file or symbol
megabrain forge  ~/repo                          # teach it your repo's file types (below)
megabrain studio ~/repo                          # studio web UI at / + the JSON API
megabrain serve-api ~/repo                       # the JSON API only, no UI
```

Scope to a sub-folder (`~/repo/src/auth`), search several repos at once
(`~/a,~/b`), and the index auto-refreshes when files change on disk.

`megabrain studio ~/repo` serves **[megabrain studio](#️-megabrain-studio--the-whole-engine-in-your-browser)**
(the web UI, above) at `/`, and `megabrain serve-api ~/repo` exposes the same JSON API
with no UI mounted. And `megabrain scan` is the studio's add-repo census on the
CLI — what *would* index and everything skipped with a reason (`.gitignore` · vendored ·
generated · too-big): `--write` applies the proposed `.megabrainignore`, and
`megabrain index --scan` indexes with those smart filters on (a plain `index` stays
byte-identical).

## Rather use the cloud?

No Claude Code or Ollama? One key runs **everything** through OpenRouter — embeddings
and narration — with sensible defaults:

```bash
export OPENROUTER_API_KEY=...
megabrain ask ~/repo "how does X work"
```

megabrain auto-picks the narrator: **Claude** when its SDK is installed, otherwise
OpenRouter. Embeddings always go through OpenRouter or a local endpoint (Anthropic has no
embeddings API).

Pin the provider and models with env vars (any OpenRouter slug):

```bash
export MEGABRAIN_CHAT_PROVIDER=openrouter                          # pin openrouter (skip claude auto-pick)
export MEGABRAIN_ASK_MODEL=google/gemini-3.1-flash-lite-preview    # the `ask` narration model
export MEGABRAIN_EMBED_MODEL=perplexity/pplx-embed-v1-0.6b         # the embedding model
```

The full provider matrix — native APIs, hybrid, fully-local GPU, per-provider defaults —
is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## 100% open-source stack (measured, no closed-weight anything)

Every default above uses a proprietary model somewhere (pplx embeddings, Gemini/Claude
narration). If you want zero closed weights — private code, an air-gapped box, or just
principle — this combo is **measured, not a guess**, and holds up:

```bash
# 1. embeddings — Apache 2.0, code-tuned, runs on your machine, $0
ollama serve
ollama pull unclemusclez/jina-embeddings-v2-base-code    # 322 MB, one time
export MEGABRAIN_EMBED_BASE_URL=http://localhost:11434/v1
export MEGABRAIN_EMBED_MODEL=unclemusclez/jina-embeddings-v2-base-code

# 2. narration — Apache 2.0 (Qwen), via OpenRouter (or self-host on the same Ollama)
export MEGABRAIN_CHAT_PROVIDER=openrouter
export MEGABRAIN_ASK_MODEL=qwen/qwen3-coder

megabrain index ~/your/repo --force
megabrain ask   ~/your/repo "how does X work"
```

**Retrieval recall** (R@1 on a 22-question golden set, sdk-server — does the right
file land #1):

| stack | R@1 | weights | cost |
|---|---|---|---|
| pplx + closed narrator *(the cloud default above)* | 0.591 | closed | ~$0.01/ask |
| **jina-code (local) + qwen3-coder** *(this section)* | 0.455 | **all open** | **$0 embed** + ~$0.01/ask on OpenRouter, or $0 fully self-hosted |

**Does `ask` actually still work?** Ran the same two real questions against
sdk-server with this exact stack:

- *"where is barge-in handled when the user interrupts mid-speech"* → correctly
  narrated from `turn_controller.py`, citing 4 files total (`event_bus.py`,
  `bot_handler.py`, `webhooks.py` too) — broader than the closed-default run.
- *"how does an inbound websocket client get authenticated"* → correctly
  narrated from `transports/client/handler.py`, the same file the closed stack found.

Both answers were grounded (every code block spliced verbatim, nothing invented) and
landed on the right file — the open stack is a **real, usable** alternative, not a
token gesture. The one real cost: `qwen/qwen3-coder` narrates in **~20-25 s** per ask
vs ~6 s for Gemini Flash — output-bound, not retrieval-bound, so it's the same
trade-off as the cloud cheap-vs-fast pick. `qwen3-coder` also runs on the *same*
local Ollama for a fully air-gapped setup (no OpenRouter call at all) — just slower
without a GPU. Full comparison + a weaker general-purpose local embedder (e5-large,
0.364 R@1) in [docs/GUIDE.md §2b](docs/GUIDE.md#2b-local-embeddings-ollama-0-code-never-leaves-your-machine).

## Compared to claude-context (measured, not vibes)

[claude-context](https://github.com/zilliztech/claude-context) (Zilliz) is the closest
open-source peer: an MCP server that also does AST-chunked, no-LLM semantic retrieval
over a repo. We actually ran it — same repo, same questions, both at their best.

**Setup:** `pinecall/sdk-server` (173 source files) · 22 natural-language questions with
hand-labelled ground-truth files (barge-in, VAD, turn control, billing…) · R@1 = the
right file ranked #1, R@5 = a right file in the top 5 unique files.

| | **megabrain** | claude-context |
|---|---|---|
| **R@1** | **0.864** | 0.818 |
| **R@5** | **1.000** | 0.909 |
| Search latency | **~22 ms** warm · ~370 ms cold | ~1400 ms |
| Vector store | **SQLite file** (zero infra) | Milvus + etcd + MinIO (3 containers) |
| Chunks for the repo | 575 | 1400 |
| LLM in the retrieval path | no | no |
| Narrated answer (`ask`) | **yes** — real code spliced in | no (returns chunks; your agent synthesizes) |

Both were given their own default embedder (megabrain: `pplx-embed-v1-0.6b`;
claude-context: `text-embedding-3-small`). To check the gap wasn't just the embedder, we
re-ran claude-context on **megabrain's exact embedder** — it scored **0.727 R@1**, i.e.
*lower*. So the difference comes from the retrieval design (tiered CORE/RELATED, import-graph
expansion, 4000-char AST merge), not from which embedding model was picked. Fine-grained
chunking (2.4× more chunks) also means its top-1 is a *fragment*, where megabrain's is a
whole file with its symbol index.

**Caveats, honestly:** one repo, 22 questions — this is an indicative result, not a
benchmark suite. More importantly, **the golden set is ours**, on a corpus megabrain has
been tuned against, so treat the absolute numbers as home-field. The reproducible parts are
the *qualitative* ones: claude-context needs a Milvus stack, returns chunks rather than a
grounded walkthrough, mixes `README.md`/`PROTOCOL.md` into code answers (it doesn't separate
docs from code), and its `get_indexing_status` reported `✅ fully indexed` while the index was
still growing in the background (200 → 1400 chunks), so an agent that trusts it will silently
search a partial index. Run it yourself before believing either of us.

## How it works

| stage | what happens |
|---|---|
| **index** | code is split over its syntax tree (whole functions / classes, never arbitrary line windows), embedded once, stored in SQLite. Incremental by hash. |
| **query** | **no LLM** — your question is embedded and matched by vector similarity. Returns every related file in ~200 ms; nothing is dropped. |
| **ask** | one LLM call narrates the answer and cites code as `[[k]]`; the engine replaces each citation with the verbatim block from disk. The model can only *point* at code, never rewrite it — so nothing is hallucinated. Broad questions fan out into parallel sub-agents, then a parent synthesizes. |
| **forge** | for a file type the engine doesn't index yet (`.toml`, `.astro`, a private DSL), an LLM writes a chunking strategy — accepted only after it partitions *every* matching file exactly. One-time, at your command, off the query path. |
| **flows** *(opt-in)* | turn it on and every `ask` caches its cross-file walkthrough; the next related question retrieves the whole workflow at once. Off by default — plain query/ask are unchanged. |

Languages: **Python · JS/TS · Markdown** built in; **Ruby · Go · Rust · PHP** with
`pip install 'megabrain[languages]'`; **anything else** via `megabrain forge` (below).

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

### `--specialize` — measure a hand-written chunker (no LLM)

For a file type the engine ALREADY reads but chunks poorly (a giant lookup
table blobs; a class of many tiny methods merges), you can hand-write a better
strategy and have the engine *measure* it before it installs:

```bash
megabrain forge ~/repo --specialize          # census: covered files the built-in chunks poorly
# write a ChunkStrategy into .megabrain/strategies/<ext>.py, then gate it:
python -c "from megabrain.forge.specialize import gate_strategy; \
           print(gate_strategy('~/repo', open('strat.py').read(), '.py'))"
```

`gate_strategy` indexes the built-in vs your candidate for real, scores span-IoU
+ hit@1 on neutral probes over every file the candidate changes, and installs
(trust-gated) **only if it beats a literature-tuned baseline** — never on a
whisper of improvement.

> **We tried letting an LLM write these and removed it.** Across four repos the
> generated chunkers lost to a five-line deterministic recipe. And the deeper,
> measured finding: on a *real* query set (the sdk-server golden) **tighter
> chunks LOWER retrieval ranking** — the 4000-char merge concentrates a file's
> evidence and that is what wins R@1 (4000 → 0.86, 2000 → 0.82, blob-split →
> 0.77). Tighter chunks help *navigation* (fewer lines to read) but not
> retrieval. **The built-in default is a genuine optimum; leave it alone unless
> you measure a win.** Specialization is for the rare pathological file, gated
> hard.

## flows — self-caching workflow retrieval (opt-in, off by default)

Every `ask` synthesizes a cross-file **workflow** ("VAD detects speech →
`TurnController.on_vad_start` → cancel TTS") that the engine used to discard.
Turn the flow cache on and it keeps them: the next related question — even
worded completely differently — retrieves the whole workflow at once.

```bash
megabrain ask ~/repo "how does X work"       # unchanged: flows are OFF by default
megabrain flows ~/repo --enable              # opt in for this repo; asks now cache their flows
megabrain index ~/repo --warm-flows 12       # or pre-fill: discover the repo's 12 top workflows now
megabrain flows ~/repo                        # list what's cached · --clear to reset
```

- **Off by default** — plain `search`/`ask` behave byte-for-byte as before, at
  zero cost. It's a mode a team turns on so its megabrain accumulates the repo's
  workflows from use (great for onboarding).
- **Rules intact:** the LLM + the one embed happen at *ask* time (write path);
  the read path is pure cosine. Flows only *add* their source files to the
  bundle when missing (never displace real files → completeness only rises), and
  the narrator gets the cached flow as non-citable context. Any flow whose cited
  files change sha is pruned on the next index — a stale walkthrough can't
  outlive its code, and `ask` splices real code regardless.

Validated on sdk-server: `--warm-flows 5` discovered and cached the system's
main workflows; a paraphrase ("how does the bot stop talking when the user cuts
in") retrieved the barge-in flow cached from a differently-worded question.

## See it live

**[bernardocastro.dev/megabrain](https://bernardocastro.dev/megabrain)** — search 7
popular open-source repos and watch the engine rank the files and pick the exact code
chunks, live. Or run it locally: `python examples/webui/server.py`.

## Learn more

- **[docs/GUIDE.md](docs/GUIDE.md)** — step-by-step: providers, indexing, the 2000-vs-4000 budget choice, custom chunkers, and the flow cache
- **[docs/STUDIO.md](docs/STUDIO.md)** — the studio web app: every view, the JSON API, and the deploy recipes
- **[docs/GRAPH.md](docs/GRAPH.md)** — the knowledge graph in plain language: what the map means, real output, and what it's actually good for
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — the full design, the locked rules, and the measurements behind them
- **[examples/](examples/)** — programmatic API · a custom `.sql` chunker · the web demo
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — the best first PR is a new language

---

<p align="center"><sub>MIT · github.com/bernatch22/megabrain</sub></p>
