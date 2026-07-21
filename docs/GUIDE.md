# Guide

The tour, front to back. Read it once and you'll know everything megabrain does.
Looking for a specific flag? → **[Reference](REFERENCE.md)**. A specific goal? →
**[Recipes](RECIPES.md)**. Why it's built this way? → **[Architecture](../ARCHITECTURE.md)**.

1. [Install and your first answer](#1-install-and-your-first-answer)
2. [The two ways to ask](#2-the-two-ways-to-ask)
3. [The studio](#3-the-studio)
4. [Map the repo with the graph](#4-map-the-repo-with-the-graph)
5. [It remembers — the flow cache](#5-it-remembers--the-flow-cache)
6. [Wire it into your coding agent](#6-wire-it-into-your-coding-agent)
7. [Teach it your file types](#7-teach-it-your-file-types)
8. [Tuning](#8-tuning)

---

## 1. Install and your first answer

```bash
pip install megabrain                 # core: Python · JS/TS · Markdown
pip install 'megabrain[languages]'    # + Ruby · Go · Rust · PHP (tree-sitter)
pip install 'megabrain[claude]'       # + narrate on Claude Code credits
```

megabrain needs **embeddings** (always) and, for `ask`, a **chat model**. They are
independent knobs — you can mix cloud embeddings with a local narrator, or the reverse.

> **Using the `claude` extra to narrate on your plan? `unset ANTHROPIC_API_KEY` first.**
> The Agent SDK drives the Claude Code CLI, and the CLI takes an API key over your login:
> with that variable exported, every `ask` bills the Anthropic API per token instead of the
> subscription — silently, with identical answers. `unset` lasts for the current shell, so
> remove it from your shell rc if that's where it comes from.

### The recommended setup

**One OpenRouter key.** The defaults are the measured-best pair, so there is nothing to
configure:

```bash
export OPENROUTER_API_KEY=sk-or-...

megabrain index ~/repo                          # once; incremental after
megabrain ask   ~/repo "how does auth work"
```

| | model | why it's the default |
|---|---|---|
| **embeddings** | `perplexity/pplx-embed-v1-0.6b` | **the best measured for code recall.** A head-to-head bakeoff beat pplx-4b, codestral-embed, openai-3-large and bge-m3 — R@1 **0.864**, bundle_full **0.955**. Perplexity-direct and via-OpenRouter score identically, so the proxy costs nothing. |
| **narration** | `google/gemini-3.1-flash-lite-preview` | **the fastest and cheapest tier** at the quality of models several times its price. `ask` is output-bound, so this is the knob that decides how long you wait. |

That combination is the one to beat: best retrieval quality, fastest narration, ~$0.002 to
index a repo and fractions of a cent per ask. Everything below is a deliberate trade-off
away from it.

### The alternatives

| instead of… | do this | trade-off |
|---|---|---|
| paying per ask | `pip install 'megabrain[claude]'`, be logged into Claude Code, and `unset ANTHROPIC_API_KEY` | narration runs on your plan (`haiku` by default); embeddings still need a key or a local endpoint |
| the cloud entirely | [run fully local](RECIPES.md#run-fully-local--no-keys-no-cloud) | $0, nothing leaves your machine; the best local embedder ties the cloud on completeness and ranks the #1 slot lower (R@1 0.773 vs 0.864) |
| the default price | `export MEGABRAIN_ASK_MODEL=qwen/qwen3-coder` | ~half the cost, ~2× slower, open weights |

The index is one SQLite file at `~/repo/.megabrain/db.sqlite`, and `ask`/`search`
auto-refresh it when files change (60 s TTL) — there is no manual re-index step. Changing
the embed model triggers a full re-embed on the next `index`, so vectors can never
silently mismatch.

Full bakeoff numbers: [Architecture §8](../ARCHITECTURE.md#8-evidence-where-the-numbers-live).

---

## 2. The two ways to ask

Everything else in megabrain is built on these two verbs — plus the graph, which reads the
same index. All three in one picture:

<p align="center">
  <img src="https://raw.githubusercontent.com/bernatch22/megabrain/master/assets/ask-agents.svg" alt="Three acts. One, search: no-LLM retrieval ranks the chunks, then the LLM rerank strikes the vocabulary-only matches and reorders what survives — app.py's prune function climbs from fourth to second, past two higher-scoring chunks, leaving the score column deliberately out of order. Two, ask: a broad question fans out into three parallel sub-agents, one synthesis merges their cited answers with the verbatim code spliced in, and the finished workflow lands in the flow cache. Three, graph: a path query between two files reports that they never call each other, names the file that bridges them, and labels every hop with the function that carries it." width="900">
</p>

<p align="center">
  <sub><b>search</b> ranks the signal, then the rerank drops the vocabulary-only look-alikes
  <b>and reorders what survives</b> — <code>app.py · prune</code> climbs past two
  higher-scoring chunks because it's the function that actually does the dropping.
  <b>ask</b> fans a broad question into parallel sub-agents and splices verbatim code into
  the synthesis. <b>graph</b> reports that two files never call each other and names the
  one that bridges them.</sub>
</p>

### `search` — the code, no LLM

```bash
megabrain search ~/repo "retry logic"            # the full bundle: CORE code + RELATED map
megabrain search ~/repo "retry logic" --prune    # flat, ranked signal chunks — noise dropped
megabrain search ~/repo "retry logic" --rerank   # + one cheap LLM pass (implies --prune)
megabrain search ~/repo "how to deploy" --docs   # the indexed markdown instead of the code
```

**`search` is code OR docs, never a blend** — like `ask`, it drops markdown from the
ranking before scoring, and `--docs` flips the whole bundle to the docs. Blending them
sounds harmless and isn't: with both in one index, a large README wins prose-shaped
questions and buries the implementation it describes (on sinatra, `README.md` took the
top slot from `lib/sinatra/base.rb` for *"how are routes defined and dispatched?"*).
Nothing blends them: `ask --with-docs` used to claim it did, but left both filters
off — so the prose won the ranking and the "code and docs" answer came back with no
code. It was removed in 0.17.1.

**Which one you want depends on which side of the API you're standing on.** `--docs` is
for *consuming* something: you're building an app on a framework and need its documented
usage now — what to call, in what order, with which options, the way its authors wrote it
down. The default is for *working on* something: contributing to Rails, or any internal
repo where the docs are thin, stale, or were never written and the source is the only
truth. That second case is most of them, which is why code is the default and the docs are
the flag.

The distinction also tells you which one to distrust. Docs describe intent and go out of
date silently; code is what actually runs. When an answer from `--docs` contradicts one
from the default, the default is right and you have just found a stale doc.

Pure retrieval: your question is embedded and matched by vector similarity, ~200 ms, free.
`--prune` keeps only the **signal** chunks — every related file still appears (each
contributes its best chunk); only the noisy chunks *inside* files are cut.

**`--rerank`** adds one buffered LLM call on top. The deterministic prune is recall-safe by
design, so files that merely *share vocabulary* with your query (tests, eval scripts)
survive as "signal" — cosine can't tell "implements scoring" from "tests scoring". The
rerank sees a compact view (ids, spans, names, no bodies) and returns the relevant ids,
ordered; the engine then reorders its **own verbatim chunks**. The model selects, it never
writes. Fail-open in every branch — no key, timeout or junk reply returns the
deterministic list untouched. *(On this repo's scoring query: 21 signal chunks → 6.)*

### `ask` — the repo, explained

```bash
megabrain ask ~/repo "how does auth work end to end"
megabrain ask ~/repo "how do the docs describe setup" --docs   # markdown instead of code
megabrain ask ~/repo "how does X work" --no-agents             # never fan out
```

One LLM call narrates the answer and cites code as `[[k]]`; **the engine replaces each
citation with the verbatim block from disk**. The model can only *point* — which is why
`ask` cannot hallucinate a line of code. The **prose around the code is still narration**:
when it matters (root-cause hunts), check its claims against the spliced code, which is
the ground truth.

**Asking about a bug? Name the state to track, not just the symptom.** Measured on a real
Rails bug (a value wiped by an `ensure` racing a deferred block): every narrator model,
weak or strong, invented a wrong mechanism for *"why is the retry enqueued immediately?"* —
while *"where along that path could `scheduled_at` be lost?"* got the correct trace. The
symptom tells the model what to explain; the named state tells it what to follow. Follow
the symptom with the variable, and you get the trace instead of a theory.

On a **broad** question `ask` becomes its own multi-agent system. A no-LLM classifier reads
the *shape* of the retrieved bundle — several core files? candidates spread across
subsystems? an issue-length query? — and if it's broad, a planner splits the bundle into up
to four scoped slices. Parallel sub-agents explain their slice, each able to call retrieval
tools on demand, and a synthesizer merges them into one walkthrough with the same global
citations. Every stage fails open to the single-agent path.

### Which one?

| your question | use | why |
|---|---|---|
| "**how/why** does X work" — a flow, cross-file behavior | **`ask`** | you want the connected story; retrieval gives you the pieces, ask assembles them |
| "give me the **code worth reading**" — you'll reason over it yourself | **`search --prune`** | flat, ranked, *with the code*, **zero LLM cost** |
| "**where** is Y" — locate a symbol or handler | **`search`** | free and instant |

Never chain `search` + your own summarization to imitate `ask`: ask's splice guarantees the
code shown is verbatim, a summary doesn't.

---

## 3. The studio

```bash
megabrain studio               # every repo you've indexed → http://localhost:2134
megabrain studio ~/repo        # …or boot straight into one
megabrain serve-api ~/repo     # the same JSON API, headless (no UI)
```

*(Screenshot on the [README](../README.md) — this section is the tour behind it.)*

Vanilla JS, no build step, no CDN, mobile-friendly. One stdlib server: `studio` mounts the
UI on top of the JSON API, `serve-api` runs the same API headless. Four tabs:

- **Ask** — watch a broad question fan out into per-agent cards, then a synthesis with the
  real code spliced in as it types. A repeat of a cached question shows a **⚡ served from
  flow cache** banner; a *related* one shows the **known flows** it pulled in as context.
  **Starter chips** sit under the bar — [every repo gets them](#starter-questions).
- **Search** — `SIGNAL · KEPT` and `NOISE · PRUNED` **side by side**, so you see what the
  engine read *and* what it threw away. Toggle the LLM rerank and the header names the
  model, how many chunks it dropped, and what it cost.

**Docs only** sits on both the Ask and the Search bar. It confines retrieval to the
indexed markdown *before* scoring, so the answer comes from the docs rather than from
code that merely mentions them — the studio's face of `ask --docs` / `search --docs`, and
the same rule of thumb applies: reach for it when you're *consuming* a project, leave it
off when you're *working on* one. It's sticky, and flipping it re-runs Search (free) but
never re-runs Ask on its own (that would spend an LLM call).
- **Flows** — [the ask cache](#5-it-remembers--the-flow-cache), listed newest-first, with
  the stored answer viewable and its cited files openable. `stale` marks flows whose
  sources changed on disk.
- **Graph** — [the knowledge graph](#4-map-the-repo-with-the-graph) on a live canvas:
  community bubbles, one community expanded, a search subgraph, or a path between two
  concepts with **`▶ Run the connection`** — a step-through of the call→definition chain.

**The code navigator** opens over any view. Click any file — a search chunk, an agent's
file pill, a graph node — and the whole file opens: real bytes, syntax-highlighted,
scrolled to the exact line. **Every identifier with a resolvable definition is a link**
(receiver-aware and import-anchored: `Path(x).resolve()` links to nothing because it's
stdlib, while `store.stats()` jumps to store.py).

**Adding a repo censuses it first** — you see exactly what will index and what's skipped
and *why* (`.gitignore` · vendored · generated · too-big), refine it in a tri-state file
tree, then watch a live progress bar index it file by file.

**Providers are live** — Claude SDK · OpenRouter · Ollama, auto-detected. Switch the
narrator without leaving the page, or start `ollama serve` in one click.

### Starter questions

Every indexed repo gets one-click chips under the ask bar. The server picks the best
source it has and labels the row honestly:

| source | where it comes from |
|---|---|
| `file` | the repo committed a **`.megabrainqueries`** at its root — authored intent wins |
| `flows` | questions already in the flow cache — their answers are **cached, so the chip serves instantly** |
| `derived` | deterministic, no-LLM questions over the repo's central files — always something |

Committing a `.megabrainqueries` pays twice: it drives the chips **and** seeds
`megabrain flows --warm`, which then caches exactly those answers instead of paying a
planner to guess the questions.

→ **[Server flags and the JSON API](REFERENCE.md#http-api)** ·
**[Run a public read-only demo](RECIPES.md#run-a-public-read-only-demo)**

---

## 4. Map the repo with the graph

```bash
megabrain graph ~/repo                                # the map
megabrain graph ~/repo --node "the scoring pipeline"  # one file — concepts resolve by embedding
megabrain graph ~/repo --path auth billing            # how two things connect
```

Every dot is a file. **Color** = its community (files that import/call each other or talk
about the same thing). **Glow** = a god node, one of the most-connected files. A **solid
line** is a real import/call edge from the AST; a **dashed line** is a *semantic* edge —
two files talking about the same thing with no code link between them.

None of this costs extra at index time; it's derived from what indexing already stored, in
milliseconds. The **only** LLM touch is one cached call that *names* the communities
(`--no-labels` skips it, fully offline).

### Real output — this repo, 122 files, 8 ms

```
[0] Search & API          81 files   the engine core: retrieval, providers, server, ask
[1] Code Chunking          7 files   chunkers/ (cAST, tree-sitter, markdown, php)
[2] Golden Query Tests     4 files   the render goldens
```

God nodes — the files everything leans on, which *is* the reading order for a newcomer:

```
providers/__init__.py   deg 37    every LLM/embedding call goes through here
retrieval/bundle.py     deg 32    the retrieval assembly
indexing/indexer.py     deg 29    the index pipeline
```

And a path names the **functions that carry each hop**, not just which files connect:

```
$ megabrain graph . --path scoring.py narrator.py
retrieval/scoring.py
└─ call → retrieval/bundle.py    · via score_chunks, chunks_for_file, search_with_state
└─ call → ask/narrator.py        · via ask, search
```

### What it's actually good for

1. **Landing on an unfamiliar repo** — communities tell you the subsystems, god nodes tell
   you the reading order, sizes tell you where the mass is.
2. **Impact estimation** — about to touch a god node? Its degree is the blast radius, and
   `--node` lists exactly who depends on it.
3. **Finding duplication** — "surprises" (≥0.85 similar, different communities, *no* code
   link) is a free near-duplicate detector. On graphify it surfaced every generated skill
   file paired with its golden twin: content maintained in two places, found automatically.
4. **"How do these two even relate?"** — `--path` answers with the real chain, or with a
   semantic hop when there is no code path, which usually means a missing abstraction.
5. **Feeding an agent** — `megabrain_graph mode=map` hands a coding agent the whole repo
   topology in one call: better planning input than any directory listing.

Coverage: Python · TS/JS · Ruby · Go · PHP have structural edges. Rust indexes without a
graph for now. → **[Thresholds and knobs](REFERENCE.md#graph)**

---

## 5. It remembers — the flow cache

**On by default.** Every `ask` synthesizes a cross-file workflow ("VAD detects speech →
`TurnController.on_vad_start` → cancel TTS"). That used to be thrown away. Now it's stored
in the same SQLite file, and the next related question — even worded completely
differently — retrieves the whole workflow at once.

| ask | time | LLM |
|---|---|---|
| first time | 27.8 s | pays once, caches |
| repeated, even reworded | **0.19 s** | **none — served from cache** |
| that question **plus another** | full narrate | the cache doesn't *cover* it — attaches as context, answers both |
| after a cited file changed | 21.9 s | sha recheck refuses the stale answer, narrates fresh |

```bash
megabrain flows ~/repo                     # list what's cached
megabrain index ~/repo --warm-flows 12     # pre-fill: discover the 12 top workflows now
megabrain flows ~/repo --refresh           # re-ask stale flows against the current code
megabrain flows ~/repo --disable           # opt this repo out
export MEGABRAIN_FLOW_CACHE=0              # kill switch, everywhere
```

**Two guards, because a cache that lies is worse than no cache:**

- **It can never describe changed code.** A flow records the sha256 of every file it cites,
  and serving re-checks each one **byte-for-byte at that instant**.
- **It can never answer half your question.** Resembling a cached question isn't enough —
  the cached one has to *cover* it. Cosine is symmetric, but "may I reuse this?" isn't: a
  compound question that *contains* a cached one scores ~1.0 against it. Ask *"how do
  filters run around a handler, **and how is a route defined?**"* with both halves cached
  separately, and the naive answer is the filters walkthrough alone with the routing half
  silently dropped. So serving also requires that nearly every content word of your
  question already appear in the cached one.

The rules hold: the LLM and the embed happen at *ask* time (the write path); the read path
is pure cosine. Flows only *add* their source files to the bundle when missing — they never
displace real files — and the narrator gets a cached flow as **non-citable** context, so it
still splices real code from disk regardless.

→ **[Turn a repo into a team knowledge base](RECIPES.md#turn-a-repo-into-a-team-knowledge-base)**

---

## 6. Wire it into your coding agent

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

Supported: **Claude Code · Codex · Antigravity · Cursor · Windsurf · Gemini CLI**. It only
ever writes the `megabrain` key — your other MCP servers are untouched — and it pins the
entry to the interpreter megabrain is installed in, so re-running it repairs a config that
drifted to an old checkout.

Your agent gets six tools, deliberately lean, because every tool costs it context and a
routing decision — pulling a single file is the host's own Read/Grep job:

| tool | when the agent reaches for it |
|---|---|
| **`megabrain_ask`** | **the default** — any "how/where/why does X work" |
| `megabrain_search` | it wants the code to read and will reason over it itself |
| `megabrain_graph` | repo topology, one file in depth, or the route between two |
| `megabrain_index` | index a new repo, or `list: true` to see every repo on the machine |
| `megabrain_forge` | make a file type the engine can't read yet searchable |
| `megabrain_flows` | manage the cache: `warm`, `refresh`, `list`, `disable` |

→ **[Every parameter](REFERENCE.md#mcp-tools)** ·
**[The one rule that makes it pay off](RECIPES.md#give-your-coding-agent-the-whole-repo)**

---

## 7. Teach it your file types

`.toml`, `.astro`, `.proto`, a private DSL — anything outside the built-in languages is
invisible to retrieval. `forge` fixes that per repo:

```bash
megabrain forge ~/repo --list        # census: which text file types aren't indexed (free)
megabrain forge ~/repo               # an LLM writes a chunker per type, validated, installed
megabrain forge ~/repo --dry-run     # show the generated code without installing
```

The one hard gate: a candidate installs **only** after it chunks every matching file in the
repo into an exact line partition — a machine-checkable oracle, so a broken chunker can't
corrupt the index. Failures feed a repair loop. The vetted module lands in
`.megabrain/strategies/<ext>.py`, sha-recorded in a user-level trust store, and loads on
every index from then on. Hand-written strategies work the same way (`megabrain trust`).

*Real run on [pallets/click](https://github.com/pallets/click): forge detected `.toml`
(11 files) and `.yaml` (8 workflows), generated both on the first attempt (~28 s), and
"which workflow runs the test suite?" went from missing entirely to ranking #1.*

> Chunking an **already-covered** type better is a different job. `--specialize` censuses
> the poorly-chunked files, you write the strategy by hand, and `gate_strategy` installs it
> only on a **measured** win. We removed the LLM from that path: across four repos the
> generated chunkers lost to a five-line deterministic recipe. Read
> [the chunk budget](#the-chunk-budget) before reaching for it.

---

## 8. Tuning

### Providers and models

Chat routing is automatic — **Claude** when its SDK is importable, otherwise OpenRouter.
Embeddings never use that switch (Anthropic has no embeddings API), so they always go to
OpenRouter or a local endpoint.

```bash
export MEGABRAIN_ASK_MODEL=qwen/qwen3-coder      # any OpenRouter slug, or a Claude alias
export MEGABRAIN_RERANK_MODEL=…                  # defaults to the ask model
```

| ask model | one ask | ≈ cost | notes |
|---|---|---|---|
| `google/gemini-3.1-flash-lite-preview` *(default)* | fastest | ~$0.007 | preview slug — pin qwen if it 404s |
| `qwen/qwen3-coder` | ~14 s | **~$0.0035** | cheapest, broader citations, open weights |
| `haiku` / `sonnet` / `opus` | ~7 s | on your plan | with `megabrain[claude]` — Claude aliases only, never an OpenRouter slug |

→ **[Cut the cost, or make it faster](RECIPES.md#make-ask-cheaper-or-faster)** ·
**[Run fully local](RECIPES.md#run-fully-local--no-keys-no-cloud)**

### The chunk budget

megabrain merges small syntax units up to **4000 non-whitespace chars**. It's the most
important knob, and the honest guidance is: **leave it alone.**

On the only human-verified query set, 4000 wins — R@1 **4000 = 0.86**, 2000 = 0.82,
8000 = 0.77. Bigger dilutes the signal; smaller fragments the evidence. Tighter chunks
*do* help navigation (fewer lines to read) but **lower retrieval quality**, because the
4000 merge concentrates a file's evidence and that is what wins the ranking. Five
"smarter" alternatives were measured and all lost.

The exception is a genuinely pathological file — a giant lookup table that becomes one
blob, or a class of many tiny methods that all merge together. There, `--specialize` lets
you write a tighter chunker for *those files only*, and it installs only if the
measurement agrees.

### Scoping and multi-repo

```bash
megabrain ask ~/repo/src/auth "how does login work"    # scope to a sub-folder
megabrain search ~/api,~/web "how do they share auth"  # several repos at once
```

Any path inside an indexed repo works — megabrain finds the root and scopes retrieval to
files under your path.

→ **[Every environment variable](REFERENCE.md#environment-variables)**
