# megabrain — usage guide

A step-by-step tour: set up a provider, index a repo, ask questions, and (only
if you want them) the advanced knobs — custom chunkers, the 2000-vs-4000 budget
choice, and the self-caching flow retrieval. Nothing here changes the defaults
unless you opt in.

---

## 1. Install

```bash
pip install megabrain                 # core: Python · JS/TS · Markdown
pip install 'megabrain[languages]'    # + Ruby · Go · Rust · PHP (tree-sitter)
pip install 'megabrain[claude]'       # + narrate ask on Claude Code credits
```

## 2. Pick a provider (embeddings + the ask model)

megabrain needs **embeddings** (always) and, for `ask`, a **chat model**. They're
independent — you can mix cloud embeddings with a local narrator, or vice versa.

### Recommended — OpenRouter for both (one key, works out of the box)

```bash
export OPENROUTER_API_KEY=sk-or-...        # env, or an `export …` line in ~/.zshrc
```

That's it. Defaults reproduce the validated stack:
- embeddings: `perplexity/pplx-embed-v1-0.6b` (1024-d int8) — **the measured
  best for code recall**; a bakeoff beat pplx-4b, codestral, openai-3-large, bge-m3.
- ask/narration: `google/gemini-3-flash-preview` — **measured ~2× faster** than
  qwen3-coder on a real walkthrough at comparable quality.

Override either by env: `MEGABRAIN_EMBED_MODEL`, `MEGABRAIN_ASK_MODEL`.

### ask model — speed vs price (measured, OpenRouter, July 2026)

| model | one `ask` | price /M (in / out) | ≈ cost/ask | notes |
|---|---|---|---|---|
| `google/gemini-3-flash-preview` *(default)* | **~6-7 s** | $0.50 / $3.00 | ~$0.007 | **2× faster**; tighter citations (~3 files); preview slug — if it 404s, set qwen below |
| `qwen/qwen3-coder` | ~14 s | **$0.22 / $1.80** | **~$0.0035** | cheapest; broader citations (6-7 files) |

Both hit the same gold file on the barge-in test (neither cites a file sitting
at bundle rank #12 — a *retrieval* limit, not the model's). Default is **gemini
for speed**; set `MEGABRAIN_ASK_MODEL=qwen/qwen3-coder` for lowest cost /
broader citations. Either way it's fractions of a cent per call — and with the
flow cache on, a **repeated question costs $0 and ~0 ms** (next up).

### Options

| you want | how |
|---|---|
| **Claude to narrate** (subscription credits, zero keys) | `pip install 'megabrain[claude]'` + be logged into Claude Code → auto-detected. Or `ANTHROPIC_API_KEY=…` to bill the API. Embeddings still need OpenRouter/local (Anthropic has no embeddings API). |
| **A specific model** | `MEGABRAIN_ASK_MODEL=anthropic/claude-haiku-4.5` (any OpenRouter slug) |
| **Gemini 3 Flash for ask (fast)** | `MEGABRAIN_ASK_MODEL=google/gemini-3-flash-preview` — **measured ~2× faster**: a real walkthrough in ~6-7 s vs qwen3-coder's ~14 s, clean and correct (cites a bit more tersely — ~3 files vs 7). Great default when you want snappy `ask`. Caveat: it's a *preview* slug (may change); `google/gemini-2.5-flash` is the stable fallback but only marginally faster than qwen (~13 s) since `ask` is output-bound. |
| **Fully local, no keys** (Ollama/LM Studio/vLLM) | see §2b below — `MEGABRAIN_EMBED_BASE_URL` + `MEGABRAIN_EMBED_MODEL` (+ `MEGABRAIN_CHAT_BASE_URL` for a local narrator too). Localhost needs no key. |
| **Perplexity direct** (not via OpenRouter) | `MEGABRAIN_EMBED_BASE_URL=https://api.perplexity.ai` + `PERPLEXITY_API_KEY=…` (auto-picked) |

## 2b. Local embeddings (Ollama, $0, code never leaves your machine)

For a private repo, or to run with zero API keys: point `MEGABRAIN_EMBED_MODEL`
at any model served by an OpenAI-compatible local endpoint. Measured 2026-07-17
against the same 22-query golden set (sdk-server snapshot, all three re-indexed
the same day on an RTX 3090 — `bundle_full` = every expected file made the
bundle, the completeness bar; `R@1` = the best file landed in the #1 slot):

```bash
ollama serve                                   # once, keep running
ollama pull hf.co/wsxiaoys/jina-embeddings-v2-base-code-Q8_0-GGUF   # 172 MB, one time

export MEGABRAIN_EMBED_BASE_URL=http://localhost:11434/v1
export MEGABRAIN_EMBED_MODEL=hf.co/wsxiaoys/jina-embeddings-v2-base-code-Q8_0-GGUF
megabrain index ~/your/repo --force            # re-embed with the new model
```

| embedding | R@1 | bundle_full | open weights? | cost | size |
|---|---|---|---|---|---|
| `perplexity/pplx-embed-v1-0.6b` *(cloud default)* | **0.864** | **0.955** | no (API-only) | ~$0.002/index | — |
| `jina-embeddings-v2-base-code` Q8 GGUF *(local, code-tuned)* | 0.682 | **0.909** | **yes — Apache 2.0** | **$0.00** | **172 MB** |
| `bge-m3` (general, local) | 0.773 | **0.909** | yes | $0.00 | 1.2 GB |

**`jina-embeddings-v2-base-code` is the local pick**: it ties bge-m3 on
`bundle_full` — the number that decides whether `ask`/`query` have the right
code to splice — at 7× less memory and 768 dims (25% smaller index, faster
search). bge-m3 ranks the #1 slot better (R@1 0.773); take it if tier1
ordering matters more than footprint. Both are 8K-context models — the
512-token BERT embedders (e5, gte, bge-large) choke on megabrain's chunks and
are not usable.

### Local narrator too (`ask` on Ollama) — the two knobs that make it work

```bash
export MEGABRAIN_CHAT_BASE_URL=http://localhost:11434/v1
export MEGABRAIN_ASK_MODEL=qwen3-coder:30b        # best measured local ask model
export MEGABRAIN_CHAT_EXTRA='{"reasoning_effort": "none"}'
export MEGABRAIN_ASK_CTX_CHARS=105000
```

- **`MEGABRAIN_CHAT_EXTRA`** — a JSON object merged into every chat request.
  Hybrid-thinking models (`qwen3:*`) burn hundreds of hidden reasoning tokens
  per answer through Ollama's OpenAI endpoint, which **ignores** the native
  `think:false`; `reasoning_effort:"none"` is the field it honors (Ollama
  ≥0.12). Non-thinking models (`qwen3-coder:*`) don't need it.
- **`MEGABRAIN_ASK_CTX_CHARS`** — `ask`'s candidate budget is sized for cloud
  windows (200K chars ≈ 50K tokens); a 40K-token local model gets its prompt
  **silently truncated** by the runtime. Cap the budget below the model's
  window (~3 chars/token, leave ~3K tokens for the answer) and raise Ollama's
  default: `OLLAMA_CONTEXT_LENGTH=40960`.

Measured same-day on the 3090 (6-query golden sample, pplx retrieval):
`qwen3-coder:30b` cite_recall 0.417 · `qwen3:14b` 0.333 · cloud `qwen/qwen3-coder`
control 0.667. Local narrators cite fewer *secondary* files — the primary answer
file and the splice guarantee hold; see `evals/LOCAL_MODELS.md` for the full lab
log.

### Does it change what `ask` actually tells you?

Retrieval R@1 is one number; what matters is whether the narrated walkthrough
still finds the right code. Ran the same two questions against sdk-server,
narrated by the same model (`gemini-3.1-flash-lite-preview`), swapping only
the embedding:

| query | pplx (cloud) cites | jina-local cites |
|---|---|---|
| *"where is barge-in handled when the user interrupts mid-speech"* | `turn_controller.py`, `bot_handler.py`, `realtime_engine.py` | `turn_controller.py`, `webhooks.py` |
| *"how does an inbound websocket client get authenticated"* | `handler.py`, `manager.py` | `handler.py` only |

Both **found the same core answer file** (`turn_controller.py` / `handler.py`)
and gave a correct, coherent walkthrough in both cases — but pplx pulled in
**more of the surrounding context** (the auth flow's `manager.py`, barge-in's
`bot_handler.py`) that jina-local missed. In practice: jina-local is good
enough to get the right answer on a focused question; pplx is more complete on
questions that span a couple of related files. Neither hallucinated — `ask`'s
splice guarantee holds regardless of which embedding retrieved the bundle.

## 3. Index and ask

```bash
megabrain index ~/repo                       # once; incremental after, auto-refreshes on change
megabrain ask   ~/repo "how does auth work"  # narrated walkthrough, real code spliced in
megabrain query ~/repo "retry logic"         # raw code map, NO LLM, ~200 ms
megabrain get   ~/repo src/x.py --symbol Foo # pull one file/symbol to expand
```

### query vs query+prune vs ask — when to use which (especially if the caller is an LLM)

Three retrieval shapes, two of them with **no LLM at all**:

- **`query`** = pure retrieval, no LLM (~200 ms, free): returns every related
  file (CORE full code + RELATED map), nothing interpreted — the full bundle.
- **`query --prune`** (`prune_noise: true`) = the same no-LLM retrieval, but it
  keeps only the **selected "signal" chunks** and returns them as a **flat list
  ranked by relevance** — each `[id] file:Lstart-end · score` with its code, the
  "noise" chunks dropped. Deterministic, zero LLM, zero token cost. Just the code
  worth reading, nothing to narrate.
- **`ask`** = `query` + one LLM narration: a walkthrough that traces the flow,
  citing code as `[[k]]`; the engine splices each citation with the **verbatim
  block from disk**, so nothing is hallucinated. Broad questions fan out into
  parallel sub-agents.

The decision rule — for a human OR an LLM agent calling megabrain:

| your question is… | use | why |
|---|---|---|
| "**how/why** does X work" — a flow, a mechanism, cross-file behavior | **ask** | you want the *connected* story; retrieval alone gives you the pieces, ask assembles them (and with flows on, the assembly gets cached) |
| "just give me the **code worth reading**" — you'll reason over it yourself, no narration | **query** (pruned) | flat, relevance-ranked signal chunks *with the code*, noise dropped, **zero LLM cost**; a modern LLM agent doesn't need pre-chewed prose, only the exact code |
| "**where** is Y" — locate a symbol/handler/config | **query** | free, instant; every related file still shows up (each contributes its best chunk) |
| you want the raw file-grouped bundle (CORE code + RELATED map) | **CLI `query`** (no `--prune`) | CLI/HTTP only — see the note below on why MCP doesn't expose it |
| the same how/why might be asked again (team repo, agents) | **ask with flows on** | first ask pays once; repeats are served free |

Rule of thumb for an agent: **query when you want just the code to read at zero LLM
cost; ask when you want a narrated cross-file walkthrough.** Never chain `query` +
your own summarization to imitate `ask` — ask's splice guarantees the code shown is
verbatim; your own summary doesn't.

#### The pruned (signal-only) shape

```bash
megabrain query ~/repo "retry logic" --prune            # flat signal chunks, ranked, with code
megabrain query ~/repo "retry logic" --prune --compact  # same, code bodies dropped (ids + spans only)
megabrain query ~/repo "retry logic" --prune --json     # machine-readable
```

**Over MCP this is the ONLY shape `megabrain_query` returns** — there is no
`prune_noise` switch and no file-grouped-bundle mode. Why: the bundle renders
RELATED as a *code-less map* (file, span, symbols), which is a dead end for an
agent over MCP — there is no `get`/`chunks` tool to expand it, so the map just
tells it code exists somewhere. Pruning has no such gap: **every file in the bundle
still appears** (each contributes its best chunk, with code), and only the noisy
chunks *inside* files are cut — so one call hands the agent real code and nothing
relevant is lost. The CLI and HTTP API still expose the full bundle.

It reuses the engine's existing signal/noise selection (a tier-1 chunk that
survives the keep-ratio cut, or a related file's best chunk) — no new scoring, no
LLM, no token cost. Use it when a coding agent just needs the exact code to READ;
reach for `ask` when it needs the
narrated story across files.

> Note: `ask` deliberately has **no** pre-filter step — filter-then-narrate would
> be double work, and a modern LLM narrator doesn't need pre-pruned prose. Pruning
> lives on the QUERY path only, and it is opt-in: a plain `query` is unchanged and
> still returns the full bundle.

---

## 4. Use it from a coding agent (MCP) — the main way

This is what megabrain is *for*: give a coding agent (Claude Code, Cursor,
Windsurf, any MCP client) one tool that replaces a dozen `grep`/`read`/explore
turns with a single grounded answer.

### Register the server

```bash
# Claude Code
claude mcp add megabrain -- python3 -m megabrain.mcp_server

# Cursor / Windsurf / generic MCP — add to the client's mcp config:
{ "mcpServers": { "megabrain": { "command": "python3",
                                 "args": ["-m", "megabrain.mcp_server"] } } }
```

The server is stdio, no daemon, no extra deps. It reads the same
`OPENROUTER_API_KEY` / model env as the CLI. Index the repos you want it to see
once (`megabrain index ~/repo`); after that the tools auto-refresh a stale index
before answering.

### The tools an agent gets

| tool | when the agent should reach for it |
|---|---|
| **`megabrain_ask`** | **the default.** Any "how/where/why does X work" — returns a senior-engineer walkthrough with the REAL code spliced in, tracing the whole cross-file flow. One call instead of crawling files. |
| `megabrain_query` | no LLM (~200 ms) — a flat, relevance-ranked list of exactly the **signal** chunks **with their code** (noise dropped, every related file still represented). When the agent wants the code to read and will reason over it itself, at zero LLM cost. |
| `megabrain_index` | index/refresh a repo the agent hasn't seen. |
| `megabrain_forge` | make a file type the engine can't read yet (`.toml`, `.astro`) searchable. |
| `megabrain_flows` | manage the opt-in flow cache: `action: "warm"` pre-caches the repo's workflows, `"refresh"` updates stale ones, `"list"` / `"enable"`. |

Five tools, on purpose. Every tool costs the calling agent context and a routing
decision, so megabrain exposes only what it alone can do — pulling a single file or
symbol is left to the host's own Read/Grep (and to `ask`'s sub-agents, which fetch
files internally). Deleting an index is a `rm -rf .megabrain` away, so there's no tool
for it either.

`scope_path` on `ask`/`query` confines the answer to a sub-folder
(`src/auth`); pass comma-separated roots to search several repos at once.

### The one rule that makes it pay off

Put this in your agent's system prompt / rules / a skill:

> **For any question about how the code works — a flow, where something is
> handled, why a value is what it is — call `megabrain_ask` FIRST, before
> grepping or reading files. One call returns the whole flow with the real code;
> only fall back to file-by-file reading if it misses.**

That single instruction is the difference between an agent that burns 15 turns
reconstructing a flow and one that gets it in a single grounded call — the code
is spliced verbatim from disk, so nothing it reads is hallucinated.

### With the flow cache on (§7), it compounds

If you `--enable` the flow cache on a team repo, each agent's `ask` leaves its
synthesized workflow in the index; the next agent (or the next question, worded
differently) retrieves that whole workflow at once — no extra tool, it rides the
same `megabrain_ask`/`megabrain_query` calls.

---

## 5. Chunk budget — 2000 vs 4000, per project

megabrain chunks code over its syntax tree and **merges small units up to a
budget** (default **4000 non-whitespace chars**). This is the single most
important tuning knob, and the honest guidance from measuring it:

**Keep 4000 (the default) for retrieval.** On the only human-verified query set
(a golden of 22 real questions), 4000 wins: R@1 **4000 = 0.86**, 2000 = 0.82,
8000 = 0.77. Bigger dilutes the signal, smaller fragments the evidence — the
4000 merge concentrates a file's evidence, which is what wins the ranking. Five
"smarter" alternatives were measured and all lost. **Don't lower it to chase
tighter chunks: tighter chunks help *navigation* (fewer lines to read) but
*lower* retrieval quality.**

**When 2000 is worth it — the exception:** a file the built-in chunks poorly —
a giant lookup **table** that becomes one blob, or a class of **many tiny
methods** that all merge together. There a query about one entry drags in the
whole file. If you mostly *navigate* such files (jump to the right span), a
tighter chunker on *those files only* is a real quality-of-life win. You don't
guess — you measure it (next section).

---

## 6. Custom chunkers — how the engine measures a strategy

### 6a. `forge` — index file types the engine can't read yet

`.toml`, `.astro`, `.proto`, a private DSL — anything outside the built-in
languages is invisible to retrieval. `forge` fixes that per repo:

```bash
megabrain forge ~/repo --list        # census: which text file types aren't indexed (free)
megabrain forge ~/repo               # an LLM writes a chunker per type, validated, installed
megabrain forge ~/repo --dry-run     # show the generated code without installing
```

The one hard gate: a candidate is only installed after it chunks **every**
matching file into an exact line partition (`validate_partition`) — a
machine-checkable oracle, so a broken chunker can't corrupt the index. The
vetted module lands in `.megabrain/strategies/<ext>.py`, sha-recorded in
`~/.megabrain/trust.json`, and loads automatically on every index thereafter.

### 6b. `--specialize` — a hand-written chunker, *measured* before it installs

For a covered type chunked poorly (the pathological file from §5 — a blob or many-tiny-methods class), you write the strategy
and the engine decides whether it earns a place. **No LLM writes it** — we tried
that and it lost to a five-line recipe four times.

```bash
megabrain forge ~/repo --specialize          # census: covered files chunked poorly
```

Then drop a `ChunkStrategy` in `.megabrain/strategies/<ext>.py` (delegate the
normal files to the built-in; only re-chunk the special shape) and gate it:

```python
from megabrain.forge_specialize import gate_strategy
print(gate_strategy("~/repo", open("mystrat.py").read(), ".py"))
```

**How the measurement works** (`forge_eval.ab_gate`, no labels, no LLM):
1. It derives neutral **probe spans** from each file's own structure (the dict
   entries, the function defs) — ground truth independent of any chunker.
2. It indexes the **built-in vs your candidate** for real and, per probe, scores
   **rank-aware span-IoU** (does the file's *top-ranked* chunk tightly cover the
   answer?) plus global **hit@1**, over **every file your candidate changes**.
3. Your candidate **installs only if** it beats a literature-tuned baseline
   (the AST chunker at 2000) on pooled IoU **and** holds hit@1 **and** regresses
   no file **and** doesn't micro-chunk (median chunk ≥ 100 chars — 1-line chunks
   game the geometry but embed as noise, and are rejected outright).

So "better" is never a vibe: it's a measured win on real retrieval, or it
doesn't install.

---

## 7. Flow cache — self-caching workflow retrieval (opt-in)

**Off by default.** Plain `query`/`ask` never touch it. Turn it on when a repo
has several devs and you want megabrain to *accumulate the team's understanding*
of the codebase.

The idea: every `ask` synthesizes a cross-file **workflow** ("VAD detects speech
→ `TurnController.on_vad_start` → cancel TTS"). Normally that's thrown away. With
the cache on, it's stored — and the next related question, even worded
completely differently, retrieves the whole workflow at once.

```bash
megabrain flows ~/repo --enable          # opt in; from now, each ask caches its flow
megabrain index ~/repo --warm-flows 12   # OR pre-fill: discover the repo's 12 top workflows now
megabrain flows ~/repo                    # list what's cached
megabrain flows ~/repo --clear           # wipe · MEGABRAIN_FLOW_CACHE=0 kills it globally
```

### How it works — and why it's safe

- **Write** (at `ask` time): the RENDERED answer (prose + real code from disk)
  is stored with `{cited file: sha}` and two vectors — question+prose (semantic
  matching) and question-only (near-exact detection). LLM + embeds run here,
  off the query path.
- **Read** (at query time): pure cosine — **no LLM in retrieval**, ever. Three
  tiers by similarity:

  | match | behavior | cost |
  |---|---|---|
  | **near-exact question** (≥ 0.88, code unchanged) | **served verbatim — no LLM.** Measured: 6.9 s → **0.02 s** on a repeat ask (345×), $0 | free |
  | **same workflow, different words** (0.62–0.88) | flow ATTACHES ("KNOWN FLOW" in the bundle + context for the narrator); narrates fresh | one LLM call |
  | below 0.62 | plain retrieval | — |

  A flow never displaces real files (its sources only *add* when missing), and
  serving re-checks every cited file's sha at that instant — **stale code is
  never served**, even inside the 60 s window before an index would prune it.
- **The narrator** gets an attached flow as *non-citable* context: it still
  cites and splices **real code from disk**, so a cached flow can only ever
  mis-prioritize, never fabricate.

### `--warm-flows` — the initial-index expander (your intuition, yes)

`megabrain index ~/repo --warm-flows N` does exactly what it sounds like: right
after the first index, an **index-time planner** reads the graph's hub files
(highest edge degree) + their doclines and writes **N research questions**
covering the system's main workflows, then runs **one `ask` per question** —
expansive queries whose whole purpose is to fill the cache. So the cache starts
full on day one instead of building up lazily. (It also *enables* the mode for
that repo.) The `ask` multi-agent fan-out and this warmup compose: a broad ask
that fans out ALSO caches its synthesized flow.

### What happens when a file changes — expire OR update

A flow records the sha of every file it cites, so a stale walkthrough can't
outlive its code. Two behaviors:

- **Expire (default, free, no LLM):** the next `index` prunes any flow whose
  cited files changed. Automatic, zero cost — the stale flow simply disappears.
- **Update (opt-in, `--refresh`):** instead of dropping it, re-run the flow's
  **original question** against the current code and regenerate the walkthrough:

  ```bash
  megabrain flows ~/repo --refresh    # reindex, then re-ask every stale flow
  ```

  This costs one `ask` per changed flow (that's why it's opt-in), and it keeps
  your cache *current* rather than just *not-wrong*. A flow whose files were all
  deleted can't be re-asked and is dropped.

---

## 8. Cheat sheet

```bash
# everyday (nothing opt-in — the tuned default)
megabrain index ~/repo
megabrain ask   ~/repo "how does X work"
megabrain query ~/repo "where is Y handled"

# teach it a new file type
megabrain forge ~/repo

# turn a repo into a team knowledge base
megabrain index ~/repo --warm-flows 12     # pre-cache the main workflows
megabrain flows ~/repo --refresh           # after big changes, update the cache

# provider knobs (env or ~/.zshrc)
OPENROUTER_API_KEY=…                        # recommended, one key for both
MEGABRAIN_ASK_MODEL=anthropic/claude-haiku-4.5
MEGABRAIN_EMBED_BASE_URL=http://localhost:11434/v1   # local embeddings (§2b)
MEGABRAIN_EMBED_MODEL=unclemusclez/jina-embeddings-v2-base-code   # code-tuned, open weights, $0
MEGABRAIN_FLOW_CACHE=0                      # hard-off the flow cache
```
