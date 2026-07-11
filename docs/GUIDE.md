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
- ask/narration: `qwen/qwen3-coder` — on par with claude-haiku on citation
  selection at ~5× lower cost (retrieval already guarantees completeness, so
  the model only points at code).

Override either by env: `MEGABRAIN_EMBED_MODEL`, `MEGABRAIN_ASK_MODEL`.

### Options

| you want | how |
|---|---|
| **Claude to narrate** (subscription credits, zero keys) | `pip install 'megabrain[claude]'` + be logged into Claude Code → auto-detected. Or `ANTHROPIC_API_KEY=…` to bill the API. Embeddings still need OpenRouter/local (Anthropic has no embeddings API). |
| **A specific model** | `MEGABRAIN_ASK_MODEL=anthropic/claude-haiku-4.5` (any OpenRouter slug) |
| **Fully local, no keys** (Ollama/LM Studio/vLLM) | `MEGABRAIN_EMBED_BASE_URL=http://localhost:11434/v1 MEGABRAIN_EMBED_MODEL=embeddinggemma` + `MEGABRAIN_CHAT_BASE_URL=…`. Localhost needs no key. ⚠️ measured caveat: small general embedders (embeddinggemma) are noticeably weaker on code than pplx — good for offline, not for best recall. |
| **Perplexity direct** (not via OpenRouter) | `MEGABRAIN_EMBED_BASE_URL=https://api.perplexity.ai` + `PERPLEXITY_API_KEY=…` (auto-picked) |

## 3. Index and ask

```bash
megabrain index ~/repo                       # once; incremental after, auto-refreshes on change
megabrain ask   ~/repo "how does auth work"  # narrated walkthrough, real code spliced in
megabrain query ~/repo "retry logic"         # raw code map, NO LLM, ~200 ms
megabrain get   ~/repo src/x.py --symbol Foo # pull one file/symbol to expand
```

- **`query`** = pure retrieval, no LLM: your question is embedded, matched by
  vector similarity, and it returns every related file (CORE full code + RELATED
  map). Fast, cheap, deterministic.
- **`ask`** = one LLM call on top: it narrates the answer and cites code as
  `[[k]]`; the engine replaces each citation with the **verbatim block from
  disk**, so nothing is hallucinated. Broad questions fan out into parallel
  sub-agents, then a parent synthesizes.

MCP (Claude Code / Cursor): `claude mcp add megabrain -- python3 -m megabrain.mcp_server`.

---

## 4. Chunk budget — 2000 vs 4000, per project

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

## 5. Custom chunkers — how the engine measures a strategy

### 5a. `forge` — index file types the engine can't read yet

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

### 5b. `--specialize` — a hand-written chunker, *measured* before it installs

For a covered type chunked poorly (5b's blob/table case), you write the strategy
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

## 6. Flow cache — self-caching workflow retrieval (opt-in)

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

- **Write** (at `ask` time): the walkthrough prose + the question are embedded in
  one call and stored with `{cited file: sha}`. The LLM and the embed run here,
  off the query path.
- **Read** (at query time): pure cosine of the query against the flow matrix —
  **no LLM in retrieval**, ever. A matching flow ATTACHES to the bundle as a
  "KNOWN FLOW" section and adds its source files only when they're missing —
  it never displaces real files, so bundle completeness can only rise.
- **The narrator** gets the cached flow as *non-citable* context: it still cites
  and splices **real code from disk**, so a cached flow can only ever
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

## 7. Cheat sheet

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
MEGABRAIN_EMBED_BASE_URL=http://localhost:11434/v1   # local embeddings
MEGABRAIN_FLOW_CACHE=0                      # hard-off the flow cache
```
